# Retinal Vasculature Quantification Framework

## Authors

Rijul S. Soans, PhD\
Susana T. L. Chung, OD, PhD

Sight Enhancement Laboratory\
Herbert Wertheim School of Optometry & Vision Science\
University of California, Berkeley, USA

## Publications

The following publication has used methods related to this framework. Additional publications will be listed here as they become available.

Soans, R. S., & Chung, S. T. L. (2026). *Altered retinal vasculature in amblyopia*. **Vision Research, 242**, 108780.\
https://doi.org/10.1016/j.visres.2026.108780

## Overview

This framework quantifies retinal vascular features from **infrared en-face localizer images** acquired using Heidelberg Spectralis optical coherence tomography (OCT). It organizes the original Heidelberg image exports, segments retinal vessels, provides a visual quality-control step, and calculates global and regional vascular measurements.

The framework supports two acquisition protocols:

- **Normal aging:** Four acquisitions per participant (one for each combination of right/left eye and horizontal/vertical scan orientation).
- **Refractive error:** Twelve acquisitions per participant (three repeats for each eye and scan orientation).

**Users should download this framework for each project separately.** Repeated measurements are not automatically averaged, and the software does not perform group-level statistical analyses.

## Features

- Organizes Heidelberg TIFF and XML exports into individual acquisitions.
- Supports four or twelve acquisitions per participant, with **97 OCT B-scans, one infrared localizer, and one XML file per acquisition**.
- Uses right-eye (OD) and left-eye (OS) axial lengths to apply the image-scale corrections used by the analysis.
- Performs automated retinal vessel, artery/vein and optic-disc segmentation.
- Provides interactive review of vessel segmentation masks before global vascular measurements are calculated.
- Calculates **vascular area, fractal dimension, vascular skeleton length, and vascular bifurcation-point count**, including the designated axial-length-corrected measurements. Python and MATLAB implementations are provided.
- Calculates **ETDRS regional vascular area, skeleton length, and bifurcation-point count** for all vessels, arteries, and veins. Regional fractal dimension is undefined and not calculated by this module.
- Exports measurements for each acquisition as CSV files.

## Software requirements

- **Windows** with [Anaconda preferred](https://www.anaconda.com/download) and **Python 3.11**, installed through the supplied Conda environment.
- **MATLAB R2022b with the Image Processing Toolbox (optional):** Used for the faster MATLAB implementation of global vascular measurements. The Python implementation is available if MATLAB is not installed.
- **Internet access for the initial installation:** Required to obtain the pretrained segmentation models. Once downloaded and verified, the models are reused from the local cache.

## Pipeline workflow

```text
Raw Heidelberg TIFF/XML exports
              |
              v
       Organize acquisitions
              |
              v
     Prepare infrared images
              |
              v
    Automated vessel segmentation
              |
              v
       Review vessel masks
              |
              v
      Global vascular metrics
         (Python or MATLAB)
              |
              v
    ETDRS regional analysis
            (optional)
```

ETDRS analysis can also be run independently of the accepted-mask step; it uses the model's segmentation outputs and image metadata.

## Installation (Windows)

### 1. Obtain the software

**Without Git (recommended for just users):** On the [project's GitHub page](https://github.com/lowvisionresearch/retinal-vasculature-quantification), choose **Code → Download ZIP** and extract the downloaded folder to a location on your computer.

**Those who wish to develop this framework further can use Git to clone the project onto their computer:**

```bash
git clone https://github.com/lowvisionresearch/retinal-vasculature-quantification.git
```

### 2. Create the Python environment

Open **Anaconda Prompt**, navigate to the extracted or cloned `retinal-vasculature-quantification` folder, and run:

```bat
conda env create -f environment.yml
conda activate sloctolyzer
```

The environment checks for three pretrained model checkpoints, downloads any missing files from this project's versioned GitHub Release, and verifies their SHA-256 checksums. Later activations reuse correctly verified files.

**Release prerequisite:** The `model-weights-v1.0.0` GitHub Release must be published with the matching checkpoint filenames and checksums before first-time automatic model downloads will work.

### 3. Open the main application

From the same folder in Anaconda Prompt:

```bat
python launch_pipeline.py
```

The graphical interface lets you select participants, run segmentation, review vessel masks, and choose whether to calculate global vascular features in Python.

## Preparing input data

Create one folder per participant inside `raw-oct-images/`. Participant identifiers may contain letters, numbers, underscores, and hyphens.
| Protocol | Acquisitions | XML files | IR localizers | OCT B-scans | Total files |
|---|---:|---:|---:|---:|---:|
| Normal aging | 4 | 4 | 4 | 388 | **396** |
| Refractive error | 12 | 12 | 12 | 1,164 | **1,188** |

Two layouts are supported:

- **Original, unorganized export:** You can place all TIFF and XML files ("flat example") exported from Spectralis directly in the participant folder. The application groups them by eye, horizontal/vertical orientation, and repeat number using the acquisition metadata.
- **Already-organized export:** You can also provide complete acquisition folders ("organized") such as `MY_02_001_OD_H_01`, each containing one XML file, `OCT.tif`, and a `bscans/` folder with 97 TIFF images.

Incomplete or mixed layouts are not processed silently; the organizer reports the problem.

### Included examples

```text
raw-oct-images/
  AG_02_001/        # Flat example: 396 TIFF/XML files
  MY_02_001/        # 12 organized acquisitions: 1,188 TIFF/XML files
examples/
  axial_lengths_sample.csv
```

These examples demonstrate both input layouts. They contain the retinal TIFF images and **sanitized XML metadata**, but not previously generated segmentations or vascular measurements. The example XML birthdates and study dates are synthetic and **must not be used to calculate participants' ages or study dates**.

The supplied axial lengths are loaded automatically into the application's working metadata. For new participants, enter the OD and OS axial lengths **in millimeters** when prompted; the values are saved for later processing. If axial-length values are corrected after processing, review the existing results before reanalysis, since image-scale corrections may change.

## Running the analysis

1. **Organize and segment:** Run `python launch_pipeline.py`, select the participant(s), and begin processing. The application validates the acquisitions and prepares the infrared images for SLOctolyzer.
2. **Review the results:** Examine each binary vessel mask in the segmentation reviewer. Accept only masks suitable for the intended analysis. Accepted masks are saved for the global vascular-feature calculations.
3. **Calculate global features:** Choose the Python option in the main application, or skip it and use the supplied MATLAB script `scripts/vessel_map_analyzer.m`. MATLAB requires the script's supporting functions on the MATLAB path.
4. **If using MATLAB:** After the MATLAB calculations finish, update the consolidated results in Anaconda Prompt:

   ```bat
   python scripts\consolidate_global_results.py
   ```

5. **Optional ETDRS analysis:** Open the regional-analysis interface:

   ```bat
   python launch_etdrs_analysis.py
   ```

   Select the intended participants, inspect the ETDRS grid overlay, and run the regional measurements. This analysis uses the model's segmentation outputs and does not depend on masks accepted for the global-feature calculations.

**Repeated acquisitions:** The `_01`, `_02`, and `_03` acquisitions are retained as separate measurements. Researchers should decide whether and how to average repeated acquisitions for subsequent statistical analyses.

**Reprocessing:** The application checks whether previously processed image inputs or image-scale metadata have changed. If it detects a mismatch, it may stop rather than silently reuse incompatible results. Review the message and back up existing outputs before intentionally reprocessing a case. The Python global-feature implementation also checks the accepted-mask checksum and eye-specific axial length; the MATLAB implementation retains its existing-results table, so changed masks or axial lengths require deliberate review before rerunning MATLAB.

## Output locations

| Folder | What it contains |
|---|---|
| `models/SLOctolyzer/analyze/demo/` | Infrared images prepared for segmentation and the input metadata workbook |
| `models/SLOctolyzer/analyze/output/` | Deep-learning segmentation results and image measurements |
| `metadata/` | Working axial-length information and cumulative acquisition metadata |
| `segmented-images/Segmented Images/` | Vessel masks accepted during review |
| `results/` | Global, ETDRS, and consolidated vascular-measurement CSV files |

These are working-output locations. Generated images and measurements are excluded from the software distribution by the repository's file-exclusion settings.

## Acknowledgements and references

This framework combines the authors' data-organization, quality-control, and vascular-feature analysis modules with third-party methods and software.

- SLOctolyzer: Burke, J., et al. (2024). *SLOctolyzer: Fully automatic analysis toolkit for segmentation and feature extracting in scanning laser ophthalmoscopy images*. **Translational Vision Science & Technology, 13**(11), 7.
- SA-UNet: Guo, C., et al. (2021). *SA-UNet: Spatial Attention U-Net for Retinal Vessel Segmentation*. **25th International Conference on Pattern Recognition**.

## Known limitations

- The organizer currently supports only complete protocols with **four or twelve acquisitions** and exactly **97 OCT B-scans per acquisition**.
- Image-scale extraction depends on appropriately structured Heidelberg XML metadata and accurate axial-length values.
- Segmentation quality must be assessed before interpreting the vascular measurements. Successful software execution does not guarantee anatomically accurate segmentation or ETDRS grid placement.
- MATLAB is optional but recommended for faster analysis of metrics, and requires its applicable toolbox and supporting functions. The MATLAB and Python implementations are separate processing options; researchers should document which one they used.
- The framework is intended for research and is not a substitute for clinical diagnosis or image-quality assessment.

## License

This repository is distributed under the GPL-3.0 license in the root `LICENSE` file. Included third-party code retains its original copyright notices and applicable license terms. Pretrained model checkpoints are distributed separately and remain subject to their applicable rights and attribution requirements.
