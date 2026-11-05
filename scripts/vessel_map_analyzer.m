%% Vessel Map Analyzer - Batch Version
%
% Reads axial lengths from:
%   metadata/axial_lengths.csv
%
% Reads segmented vessel maps from:
%   segmented-images/Segmented Images/
%
% Saves:
%   results/analysis_output_vascular_metrics_matlab.csv
%
% Also maintains:
%   vascularDensityFeatures_automated_ALcorrected.mat

clc;
clear;
close all;

%% Determine script folder and project root
scriptDir = fileparts(mfilename('fullpath'));
projectRoot = fileparts(scriptDir);

%% Output MAT-file
tableFile = fullfile(scriptDir, ...
    "vascularDensityFeatures_automated_ALcorrected.mat");

%% User-facing CSV output
resultsDir = fullfile(projectRoot, "results");

if ~isfolder(resultsDir)
    mkdir(resultsDir);
end

csvFile = fullfile(resultsDir, ...
    "analysis_output_vascular_metrics_matlab.csv");

%% Axial length CSV file
axialLengthFile = fullfile(projectRoot, ...
    "metadata", ...
    "axial_lengths.csv");

if ~isfile(axialLengthFile)
    error("Axial length metadata file not found: %s", axialLengthFile);
end

axialLengthTable = readtable(axialLengthFile, 'TextType', 'string');

requiredALColumns = ["ParticipantID", "OD_AL", "OS_AL"];

for c = 1:numel(requiredALColumns)
    if ~ismember(requiredALColumns(c), string(axialLengthTable.Properties.VariableNames))
        error("Missing required column '%s' in axial length CSV.", requiredALColumns(c));
    end
end

%% Constants for axial-length correction
C1 = 3.3944;
C2 = 0.01306;
C3 = 1.82;

%% Load or initialize corrected table
AL_map = containers.Map('KeyType', 'char', 'ValueType', 'any');

if isfile(tableFile)

    S = load(tableFile);

    if isfield(S, 'vascularDensityFeatures_automated_ALcorrected')
        vascularDensityFeatures_automated_ALcorrected = ...
            S.vascularDensityFeatures_automated_ALcorrected;

        fprintf("Loaded existing corrected table from:\n%s\n\n", tableFile);
    else
        error("MAT-file exists but variable 'vascularDensityFeatures_automated_ALcorrected' was not found.");
    end

else

    vascularDensityFeatures_automated_ALcorrected = table( ...
        strings(0, 1), ...
        strings(0, 1), ...
        strings(0, 1), ...
        zeros(0, 1), ...
        zeros(0, 1), ...
        zeros(0, 1), ...
        zeros(0, 1), ...
        zeros(0, 1), ...
        zeros(0, 1), ...
        zeros(0, 1), ...
        'VariableNames', { ...
        'Category', ...
        'Filename', ...
        'Eye', ...
        'OD_OS_AL_Average', ...
        'Vascular_Area', ...
        'Corrected_Vascular_Area', ...
        'Fractal_Dimension', ...
        'Vascular_Skeleton_Length', ...
        'Corrected_Vascular_Skeleton_Length', ...
        'Vascular_Bifurcation_Points' ...
        });

    save(tableFile, "vascularDensityFeatures_automated_ALcorrected");

    fprintf("Created new corrected table and saved to:\n%s\n\n", tableFile);
end

%% Determine segmented image folder
imagesFolder = fullfile(projectRoot, ...
    "segmented-images", ...
    "Segmented Images");

if ~isfolder(imagesFolder)
    error("Segmented image folder not found: %s", imagesFolder);
end

%% Get all PNG files
imageFiles = [
    dir(fullfile(imagesFolder, "*.png"));
    dir(fullfile(imagesFolder, "*.PNG"))
    ];

[~, uniqueIdx] = unique(fullfile({imageFiles.folder}, {imageFiles.name}));
imageFiles = imageFiles(uniqueIdx);

if isempty(imageFiles)
    fprintf("No PNG files found in folder:\n%s\n", imagesFolder);
    return;
end

fprintf("Selected folder:\n%s\n", imagesFolder);
fprintf("Found %d PNG files.\n\n", numel(imageFiles));

%% Counters
numProcessedThisRun = 0;
numSkippedExisting = 0;
numSkippedUnknown = 0;
numSkippedMissingAL = 0;
stopRequested = false;

%% Batch loop
for k = 1:numel(imageFiles)

    chosenFile = string(imageFiles(k).name);
    fullFilePath = fullfile(imageFiles(k).folder, imageFiles(k).name);

    fprintf('------------------------------------------------------------\n');
    fprintf('Checking file %d of %d: %s\n', k, numel(imageFiles), chosenFile);

    %% Skip if already processed
    if any(vascularDensityFeatures_automated_ALcorrected.Filename == chosenFile)
        fprintf("Already processed in corrected table. Skipping.\n\n");
        numSkippedExisting = numSkippedExisting + 1;
        continue;
    end

    %% Ask whether to continue
    sessionChoice = questdlg( ...
        sprintf("Next file:\n%s\n\nDo you want to process this file?", chosenFile), ...
        "Continue Batch Session", ...
        "Process", "Stop Session", "Process");

    switch sessionChoice
        case "Process"
            % Continue
        otherwise
            fprintf("User chose to stop before processing %s.\n", chosenFile);
            stopRequested = true;
            break;
    end

    %% Infer metadata from filename
    eye = inferEyeFromFilename(chosenFile);
    category = "Unspecified"; % Do not infer a study label from acquisition count.

    if eye == "Unknown"
        warning("Could not infer eye from filename: %s. Skipping this file.", chosenFile);
        numSkippedUnknown = numSkippedUnknown + 1;
        continue;
    end

    participantID = extractParticipantID(chosenFile);

    %% Get axial lengths from CSV once per participant
    if ~isKey(AL_map, char(participantID))

        rowIdx = axialLengthTable.ParticipantID == participantID;

        if ~any(rowIdx)
            warning("No axial length values found for participant: %s. Skipping file.", participantID);
            numSkippedMissingAL = numSkippedMissingAL + 1;
            continue;
        end

        odAL = axialLengthTable.OD_AL(find(rowIdx, 1, 'first'));
        osAL = axialLengthTable.OS_AL(find(rowIdx, 1, 'first'));

        if ismissing(odAL) || ismissing(osAL) || isnan(double(odAL)) || isnan(double(osAL))
            warning("Invalid axial length values for participant: %s. Skipping file.", participantID);
            numSkippedMissingAL = numSkippedMissingAL + 1;
            continue;
        end

        AL_map(char(participantID)) = [double(odAL), double(osAL)];

        fprintf("Loaded axial lengths for %s from CSV: OD = %.3f mm, OS = %.3f mm\n", ...
            participantID, double(odAL), double(osAL));
    end

    ALs = AL_map(char(participantID));

    if eye == "OD"
        AL = ALs(1);
    elseif eye == "OS"
        AL = ALs(2);
    else
        error("Unknown eye value '%s'.", eye);
    end

    %% Read segmented vessel map
    vessel_orig_img = imread(fullFilePath);

    if ndims(vessel_orig_img) == 3
        vesselMask = vessel_orig_img(:, :, 1) > 0;
    else
        vesselMask = vessel_orig_img > 0;
    end

    cleanedVesselPixels = vesselMask;

    %% Show cleaned vessel mask
    hMask = figure('Name', sprintf('Cleaned Vessel Mask - %s', chosenFile), ...
        'NumberTitle', 'off');
    imshow(cleanedVesselPixels);
    title(sprintf("Cleaned Vessel Mask: %s", chosenFile), 'Interpreter', 'none');

    %% 1. Vascular Area
    vascularArea = nnz(cleanedVesselPixels);
    fprintf("Vascular Area: %d pixels\n", vascularArea);

    %% 2. Fractal Dimension
    blockPlotOn = false;
    numScales = 20;
    blockSizeDisplay = 12;
    graphPlotOn = false;

    [fractalDimension, ~, ~] = calculateVascularFractalDimension( ...
        cleanedVesselPixels, numScales, blockSizeDisplay, blockPlotOn, graphPlotOn);

    fprintf("Fractal Dimension: %.4f\n", fractalDimension);

    %% 3. Vascular Skeleton Length
    skeletonVesselImage = bwmorph(cleanedVesselPixels, "skeleton", Inf);
    spurSkelRemovImage = bwmorph(skeletonVesselImage, "spur", 5);
    vascularSkeletonLength = nnz(spurSkelRemovImage);

    fprintf("Vascular Skeleton Length: %d pixels\n", vascularSkeletonLength);

    %% 4. Vascular Bifurcation Points
    [branchPoints, vbpImage] = detectVBP(spurSkelRemovImage);
    numBifurcationPoints = nnz(branchPoints);

    fprintf("Detected No. of Bifurcation Points: %d\n", numBifurcationPoints);

    %% Create RGB skeleton image with bifurcations overlaid
    RGBSkelImage = repmat(uint8(vbpImage) * 255, 1, 1, 3);
    [rowBifur, colBifur] = find(branchPoints);

    for idx = 1:numel(rowBifur)
        RGBSkelImage = insertShape(RGBSkelImage, 'FilledCircle', ...
            [colBifur(idx), rowBifur(idx), 5], ...
            'Color', 'green', 'Opacity', 0.5);
    end

    %% Show review figure
    hInspect = figure('Name', sprintf('Bifurcation Review - %s', chosenFile), ...
        'NumberTitle', 'off');
    imshow(RGBSkelImage);
    title({
        sprintf('Skeleton + Bifurcations: %s', chosenFile), ...
        sprintf('Detected Vascular Bifurcation Points = %d', numBifurcationPoints)
        }, 'Interpreter', 'none');

    drawnow;

    %% Review step
    reviewChoice = questdlg( ...
        sprintf(['Detected vascular bifurcation points = %d\n\n' ...
        'Choose an action:'], numBifurcationPoints), ...
        'Review Vascular Bifurcation Points', ...
        'Okay', 'Not Okay', 'Stop Session', 'Okay');

    switch reviewChoice
        case 'Okay'
            finalBifurcationPoints = numBifurcationPoints;

        case 'Not Okay'
            hCorrection = figure('Name', sprintf('Correction View - %s', chosenFile), ...
                'NumberTitle', 'off');
            tiledlayout(1, 2, 'Padding', 'compact', 'TileSpacing', 'compact');

            nexttile;
            imshow(RGBSkelImage);
            title(sprintf('RGBSkelImage\nDetected = %d', numBifurcationPoints), ...
                'Interpreter', 'none');

            nexttile;
            imshow(branchPoints);
            title('branchPoints');

            drawnow;

            userInput = input(sprintf(['File: %s\n' ...
                'Enter the CORRECT number of vascular bifurcation points\n' ...
                '(or type STOP to end the session): '], ...
                chosenFile), 's');

            if strcmpi(strtrim(userInput), 'STOP')
                fprintf("User requested session stop during manual correction.\n");
                closeIfValid(hInspect);
                closeIfValid(hCorrection);
                closeIfValid(hMask);
                stopRequested = true;
                break;
            end

            correctedValue = str2double(strtrim(userInput));

            while isnan(correctedValue) || correctedValue < 0 || mod(correctedValue, 1) ~= 0
                userInput = input('Invalid input. Please enter a non-negative integer, or type STOP: ', 's');

                if strcmpi(strtrim(userInput), 'STOP')
                    fprintf("User requested session stop during manual correction.\n");
                    closeIfValid(hInspect);
                    closeIfValid(hCorrection);
                    closeIfValid(hMask);
                    stopRequested = true;
                    break;
                end

                correctedValue = str2double(strtrim(userInput));
            end

            if stopRequested
                break;
            end

            finalBifurcationPoints = correctedValue;
            fprintf("User-corrected No. of Bifurcation Points: %d\n", finalBifurcationPoints);

            closeIfValid(hCorrection);

        case 'Stop Session'
            fprintf("User chose to stop during bifurcation review.\n");
            closeIfValid(hInspect);
            closeIfValid(hMask);
            stopRequested = true;
            break;

        otherwise
            fprintf("Review dialog was closed. Stopping session cleanly.\n");
            closeIfValid(hInspect);
            closeIfValid(hMask);
            stopRequested = true;
            break;
    end

    if stopRequested
        break;
    end

    %% Compute corrected metrics
    scaleFactor = C1 * C2 * (AL - C3);
    correctedVascularArea = scaleFactor * vascularArea;
    correctedVascularSkeletonLength = scaleFactor * vascularSkeletonLength;

    %% Append new row
    newRow = table( ...
        category, ...
        chosenFile, ...
        eye, ...
        AL, ...
        vascularArea, ...
        correctedVascularArea, ...
        fractalDimension, ...
        vascularSkeletonLength, ...
        correctedVascularSkeletonLength, ...
        finalBifurcationPoints, ...
        'VariableNames', vascularDensityFeatures_automated_ALcorrected.Properties.VariableNames);

    vascularDensityFeatures_automated_ALcorrected = ...
        [vascularDensityFeatures_automated_ALcorrected; newRow];

    %% Save immediately
    save(tableFile, "vascularDensityFeatures_automated_ALcorrected");
    writetable( ...
        vascularDensityFeatures_automated_ALcorrected, ...
        csvFile);
    fprintf("Saved corrected results for: %s\n\n", chosenFile);

    numProcessedThisRun = numProcessedThisRun + 1;

    %% Close figures
    closeIfValid(hInspect);
    closeIfValid(hMask);
end

%% Ensure CSV reflects the complete current table
writetable( ...
    vascularDensityFeatures_automated_ALcorrected, ...
    csvFile);

%% Final summary
fprintf('============================================================\n');
if stopRequested
    fprintf('Session stopped cleanly by user.\n');
else
    fprintf('Batch processing complete.\n');
end
fprintf('Newly processed in this run: %d\n', numProcessedThisRun);
fprintf('Already existing / skipped:  %d\n', numSkippedExisting);
fprintf('Unknown filename format:     %d\n', numSkippedUnknown);
fprintf('Missing axial length values: %d\n', numSkippedMissingAL);
fprintf('Total rows in corrected table: %d\n', ...
    height(vascularDensityFeatures_automated_ALcorrected));
fprintf('MAT-file saved in:\n%s\n', tableFile);
fprintf('CSV results saved in:\n%s\n', csvFile);
fprintf('============================================================\n\n');

disp(vascularDensityFeatures_automated_ALcorrected);

%% Local functions
function eye = inferEyeFromFilename(fname)
fname = string(fname);

if contains(fname, "_OD_")
    eye = "OD";
elseif contains(fname, "_OS_")
    eye = "OS";
else
    eye = "Unknown";
end
end

function participantID = extractParticipantID(fname)
% Parse from the end so IDs such as AG_02_001 and STUDY_2030_ALPHA work.
fname = string(fname);
pattern = '^(.*)_(OD|OS)_(H|V)_[0-9]{2}(?:_slo_binary_map)?\.[Pp][Nn][Gg]$';
parts = regexp(char(fname), pattern, 'tokens', 'once');

if isempty(parts)
    error("Filename does not match expected format: %s", fname);
end

participantID = string(parts{1});
end

function closeIfValid(h)
if ~isempty(h) && isgraphics(h)
    close(h);
end
end