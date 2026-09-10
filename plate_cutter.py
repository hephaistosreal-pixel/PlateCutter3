#!/usr/bin/env python3
"""
plate_cutter.py

Cuts a photo of one-or-more 96-well plates (8 columns x 12 rows each,
side by side) into one narrow image per column (12 wells stacked
top-to-bottom).
"""
import argparse
import os
import re

import cv2
import numpy as np
from scipy.signal import find_peaks
from scipy.ndimage import uniform_filter1d

ROWS_PER_PLATE = 12
COLS_PER_PLATE = 8

EXCLUDE_KEYWORDS = {"AGG", "CTRL", "TEST", "RUN", "SET", "REP", "POS", "NEG", "PLATE"}


def get_profiles(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].astype(float)
    col_profile = uniform_filter1d(sat.mean(axis=0), size=7)
    row_profile = uniform_filter1d(sat.mean(axis=1), size=7)
    return col_profile, row_profile


def estimate_pitch(profile, rough_distance):
    peaks, _ = find_peaks(profile, distance=rough_distance, prominence=3)
    diffs = np.diff(np.sort(peaks))
    if len(diffs) == 0:
        return rough_distance, peaks
    med = np.median(diffs)
    good = diffs[(diffs > med * 0.7) & (diffs < med * 1.4)]
    return (np.median(good) if len(good) else med), peaks


def fit_grid(profile, n_points, pitch, search_lo, search_hi):
    best_score, best_offset = -1, search_lo
    step = max(1, int(pitch // 8))
    max_offset = search_hi - (n_points - 1) * pitch
    
    if max_offset < search_lo:
        max_offset = search_lo

    for offset in np.arange(search_lo, max_offset + step, step):
        idx = (offset + np.arange(n_points) * pitch).round().astype(int)
        if idx.max() >= len(profile) or idx.min() < 0:
            continue
        score = profile[idx].sum()
        if score > best_score:
            best_score, best_offset = score, offset

    idx = (best_offset + np.arange(n_points) * pitch).round().astype(int)

    window = int(pitch * 0.3)
    refined = []
    for i in idx:
        lo, hi = max(0, i - window), min(len(profile), i + window + 1)
        local = profile[lo:hi]
        if len(local) > 0:
            refined.append(lo + int(np.argmax(local)))
        else:
            refined.append(i)
    return np.array(refined)


def split_into_plates(col_peaks, pitch, gap_factor=1.7):
    if len(col_peaks) == 0:
        return []
    diffs = np.diff(col_peaks)
    groups = [[col_peaks[0]]]
    for peak, d in zip(col_peaks[1:], diffs):
        if d > gap_factor * pitch:
            groups.append([])
        groups[-1].append(peak)
    return [np.array(g) for g in groups]


def detect_grid(img, expected_n_plates=None):
    col_profile, row_profile = get_profiles(img)
    h, w = img.shape[:2]

    row_pitch, rough_row_peaks = estimate_pitch(row_profile, rough_distance=h / (ROWS_PER_PLATE + 3))

    row_groups = split_into_plates(np.sort(rough_row_peaks), row_pitch, gap_factor=1.35) if len(rough_row_peaks) else []
    if row_groups:
        best_row_group = min(row_groups, key=lambda g: abs(len(g) - ROWS_PER_PLATE))
        row_lo, row_hi = int(best_row_group.min()), int(best_row_group.max())
    else:
        row_lo, row_hi = 0, h

    row_peaks = fit_grid(row_profile, ROWS_PER_PLATE, row_pitch, search_lo=row_lo, search_hi=row_hi)

    col_pitch, rough_col_peaks = estimate_pitch(col_profile, rough_distance=w / 30)

    if expected_n_plates is not None:
        n_plates = expected_n_plates
    else:
        n_plates = max(1, round(w / (col_pitch * COLS_PER_PLATE)))

    peaks_r, props_r = find_peaks(col_profile, distance=max(1, w / 30), prominence=2, height=0)
    if len(props_r["peak_heights"]):
        h_thresh = 0.4 * np.median(props_r["peak_heights"])
        strong = peaks_r[props_r["peak_heights"] >= h_thresh]
    else:
        strong = peaks_r

    rough_sorted = np.sort(strong)
    rough_groups = split_into_plates(rough_sorted, col_pitch) if len(rough_sorted) else []

    plates = []
    if len(rough_groups) == n_plates:
        for g in rough_groups:
            lo, hi = g.min() - col_pitch, g.max() + col_pitch
            grid = fit_grid(col_profile, COLS_PER_PLATE, col_pitch,
                            search_lo=max(0, lo), search_hi=min(w, hi))
            plates.append(grid)
    else:
        margin = w * 0.05
        usable_w = w - (2 * margin)
        seg_w = usable_w / n_plates
        for p in range(n_plates):
            lo = margin + (p * seg_w)
            hi = margin + ((p + 1) * seg_w)
            grid = fit_grid(col_profile, COLS_PER_PLATE, col_pitch,
                            search_lo=lo, search_hi=hi)
            plates.append(grid)

    return plates, row_peaks, col_pitch, row_pitch


def parse_plate_labels(filename):
    base = os.path.basename(filename)
    matches = re.findall(r'_([A-Za-z]{1,4})_', base)
    for match in matches:
        upper_m = match.upper()
        if upper_m in EXCLUDE_KEYWORDS:
            continue
        labels = list(upper_m)
        return labels, len(labels)
    return None, None


def extract_timepoint(filename):
    stem = os.path.splitext(os.path.basename(filename))[0]
    lower = stem.lower().rstrip('_-')
    if lower.endswith("20"):
        return "20"
    elif lower.endswith("90"):
        return "90"
    return None


def extract_result_prefix(filename):
    stem = os.path.splitext(os.path.basename(filename))[0]
    prefix = stem.split('_')[0]
    return prefix if prefix else stem


def crop_columns(img, plates, row_peaks, col_pitch, row_pitch):
    h, w = img.shape[:2]
    top = int(round(row_peaks[0] - row_pitch / 2))
    bottom = int(round(row_peaks[-1] + row_pitch / 2))
    top, bottom = max(0, top), min(h, bottom)

    result = {}
    for p_idx, cols in enumerate(plates):
        crops = []
        for x_c in cols:
            left = int(round(x_c - col_pitch / 2))
            right = int(round(x_c + col_pitch / 2))
            left, right = max(0, left), min(w, right)
            crops.append(img[top:bottom, left:right])
        result[p_idx] = crops
    return result


def next_results_dir(outdir):
    candidate = os.path.join(outdir, "Results")
    if not os.path.isdir(candidate):
        return candidate
    i = 2
    while os.path.isdir(os.path.join(outdir, f"Results_{i}")):
        i += 1
    return os.path.join(outdir, f"Results_{i}")


def process_image(path, results_root):
    # Windows path safe image read
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        print(f"!! Could not read {path}, skipping.")
        return

    timepoint = extract_timepoint(path)
    if timepoint is None:
        print(f"!! SKIPPED '{os.path.basename(path)}': filename doesn't end in '20' or '90'.")
        return

    labels, expected_n_plates = parse_plate_labels(path)
    plates, row_peaks, col_pitch, row_pitch = detect_grid(img, expected_n_plates=expected_n_plates)

    if labels is None:
        labels = [chr(ord('A') + i) for i in range(len(plates))]

    prefix = extract_result_prefix(path)
    crops_by_plate = crop_columns(img, plates, row_peaks, col_pitch, row_pitch)

    result_dir = os.path.join(results_root, timepoint)
    os.makedirs(result_dir, exist_ok=True)

    for p_idx, crops in crops_by_plate.items():
        label = labels[p_idx] if p_idx < len(labels) else str(p_idx + 1)
        for c_idx, crop in enumerate(crops, start=1):
            col_letter = chr(ord('a') + c_idx - 1)
            out_name = f"{prefix}_{label}_{timepoint}_{col_letter}.jpg"
            out_path = os.path.join(result_dir, out_name)
            if os.path.exists(out_path):
                base_name, ext = os.path.splitext(out_name)
                counter = 2
                while os.path.exists(os.path.join(result_dir, f"{base_name}_{counter}{ext}")):
                    counter += 1
                out_path = os.path.join(result_dir, f"{base_name}_{counter}{ext}")
            cv2.imwrite(out_path, crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        print(f"  plate {label}: {len(crops)} columns -> {result_dir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="+", help="plate photo(s) to process")
    ap.add_argument("-o", "--outdir", default="plate_columns", help="output folder")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    results_root = next_results_dir(args.outdir)
    print(f"Saving results in: {results_root}")
    for path in args.images:
        print(f"Processing {path} ...")
        try:
            process_image(path, results_root)
        except Exception as e:
            print(f"  FAILED: {e}")


if __name__ == "__main__":
    main()