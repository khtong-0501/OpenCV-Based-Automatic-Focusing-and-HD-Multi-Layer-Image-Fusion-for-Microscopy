# Microscope Auto-Focus & 3D Fusion System: User Manual

## 1. System Overview
This system is an integrated solution for automated microscope focusing, high-definition multi-focal image fusion, and 3D visualization. It is designed to overcome the limitations of traditional optical microscopy, such as shallow depth of field and manual focusing errors.

## 2. System Architecture
The system pipeline consists of four main modules:
1. **Data Acquisition**: Capturing multi-focal plane images via live camera or video import.
2. **Preprocessing & Alignment**: Laplacian-based quality filtering and ECC-based image registration.
3. **Fusion & Optimization**: Pixel-level depth-of-field fusion and interactive background removal.
4. **Visualization**: 3D reconstruction and interactive web-based viewing.

## 3. Workflow Guide

### Phase 1: Input & Parameters
* **Mode Selection**: Choose between "Live CAM" or "Recorded Video."
* **Capture Settings**: Configure sampling intervals and output filenames.
* **Processing**: The system automatically filters blurry frames, aligns images using ECC, and merges them into a single clear image (`stacked.png`).

### Phase 2: ROI Selection
* **Action**: Draw a bounding box around the target specimen.
* **Shortcut**: Press `Enter` to confirm or `Esc` to return to the home menu.

### Phase 3: Interactive Matting Editor
A specialized interface for background removal and image refinement:
* **Left Click**: Select and remove colors (BGR tolerance).
* **Right Click**: Undo color removal.
* **B Key**: Toggle circular brush for manual refinement.
* **T Key**: Generate a 3D PLY model via Cloud API (Tripo).
* **3 Key**: Open a 360° interactive HTML preview.
* **S Key**: Save and proceed to final export.

### Phase 4: Output & Export
* **2D Image**: Exported as a transparent RGBA PNG.
* **3D Viewer**: Interactive HTML files with multi-layer depth stacks.

## 4. Key Controls Quick Sheet

| Action | Shortcut |
| :--- | :--- |
| **Undo Action** | `U` |
| **Increase Brush** | `+` |
| **Decrease Brush** | `-` |
| **Save & Next** | `S` |
| **Quit Editor** | `Q` or `Esc` |

---
*Developed by the Macau Fong Chong School Project Team.*
