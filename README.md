# 基於OpenCv顯微鏡自動對焦以及高清分層合併：使用者說明書
作者：Martin TONG, Jason TONG, Jacky SIT

單位：Escola Dos Moradores De Macau

檔案鏈接（Google 雲端）: https://drive.google.com/file/d/16daq7fprnsxukdZ4S2WjzEJeX-OwqW67/view?usp=drive_link

## 1. 系統概覽
本系統是一款整合顯微鏡自動對焦、高清多焦面影像融合與 3D 可視化的一體化解決方案。系統旨在解決傳統光學顯微鏡景深極淺、容易因手動對焦失誤導致影像模糊的難題。

## 2. 系統架構
系統流水線由四大模組組成：
1. **數據採集**：透過即時攝影機或匯入本地影片採集多焦平面影像。
2. **預處理與對齊**：基於拉普拉斯方差（Laplacian Variance）進行清晰度篩選，並利用 ECC 演算法進行影像配準。
3. **融合與優化**：進行像素級景深擴展（EDoF）融合，並提供互動式背景移除功能。
4. **視覺化**：生成 3D 模型與支援網頁互動的環繞瀏覽器。

## 3. 工作流程指南

### 第一階段：輸入與參數設定
* **模式選擇**：選擇「即時攝影機 (Live CAM)」或「匯入影片」。
* **採集設定**：設定抽幀間隔（秒）與輸出檔名。
* **處理程序**：系統自動剔除模糊畫面，配準影像並合併為全焦清晰圖 (`stacked.png`)。

### 第二階段：感興趣區域 (ROI) 選取
* **操作**：使用滑鼠在畫面拖曳出目標物矩形框。
* **捷徑**：按下 `Enter` 確認；按下 `Esc` 返回主選單。

### 第三階段：互動式去背編輯器
提供專業的影像精修介面：
* **滑鼠左鍵**：選取並移除特定色彩（基於 BGR 容差）。
* **滑鼠右鍵**：撤銷色彩移除操作。
* **B 鍵**：切換圓形筆刷，用於手動修復細節。
* **T 鍵**：透過 Tripo API 生成 3D PLY 模型。
* **3 鍵**：生成 360° 環繞 HTML 互動預覽頁。
* **S 鍵**：儲存並進入最終輸出流程。

### 第四階段：成果輸出
* **2D 影像**：輸出為透明背景的 RGBA PNG 圖檔。
* **3D 檢視器**：包含多層深度資訊的互動式 HTML 網頁。

## 4. 快捷鍵速查表

| 操作功能 | 快捷鍵 |
| :--- | :--- |
| **撤銷上一步** | `U` |
| **增加筆刷半徑** | `+` |
| **縮小筆刷半徑** | `-` |
| **儲存並進入下一階段** | `S` |
| **結束編輯器** | `Q` 或 `Esc` |

# Microscope Auto-Focus & 3D Fusion System: User Manual
Author: Martin TONG, Jason TONG, Jacky SIT

Unit: Escola Dos Moradores De Macau

Software link: https://drive.google.com/file/d/16daq7fprnsxukdZ4S2WjzEJeX-OwqW67/view?usp=drive_link

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
