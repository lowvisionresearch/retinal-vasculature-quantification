function [fractal_dimension, log_x, log_y] = calculateVascularFractalDimension(image, numScales, blockSizeDisplay, blockPlotOn, graphPlotOn)
% Computes box‐counting fractal dimension on a binary image.

% Check if image is binary
if numel(unique(image)) > 2
    error('Input image must be a binary image.');
end

[rows, cols] = size(image);

% Define smallest and largest divisions
minDiv = 3;
maxDiv = floor(min(rows, cols));  % so box size ≥ 1 px

% Generate log‐spaced division factors, then remove duplicates
division_factors = unique(round(logspace(log10(minDiv), log10(maxDiv), numScales)));

% Actual number of distinct scales
numDivisions = numel(division_factors);

% Preallocate log vectors
log_x = zeros(numDivisions, 1);
log_y = zeros(numDivisions, 1);

for divIdx = 1:numDivisions
    n = division_factors(divIdx);

    % exact (fractional) box size for plotting
    box_size = rows / n;
    reciprocal_box_size = 1 / box_size;

    % integer block sizes for counting
    block_size_row = floor(rows / n);
    block_size_col = floor(cols / n);

    % count how many boxes contain at least one vessel pixel
    count_blocks_with_vessels = 0;
    for i = 0:n-1
        row_start = i*block_size_row + 1;
        row_end = min(rows, (i+1)*block_size_row);
        for j = 0:n-1
            col_start = j*block_size_col + 1;
            col_end = min(cols, (j+1)*block_size_col);
            block = image(row_start:row_end, col_start:col_end);
            if any(block(:))
                count_blocks_with_vessels = count_blocks_with_vessels + 1;
            end
        end
    end

    log_x(divIdx) = log10(reciprocal_box_size);         % log(1/box size)
    log_y(divIdx) = log10(count_blocks_with_vessels);   % log(N(s))
end

% Perform linear regression in log–log space
coeffs = polyfit(log_x, log_y, 1);
fractal_dimension = coeffs(1); % The slope of the regression line is the fractal dimension

% Optionally show Fractal Dimension fit
if graphPlotOn
    figure;
    scatter(log_x, log_y, 50, 'b', 'filled');
    hold on;
    x_fit = linspace(min(log_x), max(log_x), 200);
    y_fit = polyval(coeffs, x_fit);
    plot(x_fit, y_fit, 'r-', 'LineWidth', 2);
    set(gca, 'FontName', 'Helvetica', 'FontSize', 16);
    xlabel('log(1/box size)');
    ylabel('log(# boxes with vessels)');
    title('Regression Analysis for Vascular Fractal Dimension');
    grid on;
    set(gcf, 'Color', 'w');
    hold off;
end

% Optionally show the image with blockSize * blockSize bounding boxes
if blockPlotOn && blockSizeDisplay > 0
    num_blocks = blockSizeDisplay;
    block_size_row = floor(rows/num_blocks);
    block_size_col = floor(cols/num_blocks);
    figure;
    imshow(image);
    hold on;
    for i = 0:num_blocks-1
        for j = 0:num_blocks-1
            row_start = i*block_size_row + 1;
            col_start = j*block_size_col + 1;
            rectangle('Position', [col_start, row_start, block_size_col, block_size_row], 'EdgeColor', 'g');
        end
    end
    set(gcf, 'Color', 'w');
    hold off;
end

end
