function [branchPoints, graphImg] = detectVBP(spurSkelRemovImage)

graphImg = false(size(spurSkelRemovImage));
[adjMatrix, nodeStruct, edgeStruct] = Skel2Graph(spurSkelRemovImage, 0);
numNodes = numel(nodeStruct);
numEdges = numel(edgeStruct);

for e = 1:numEdges
    u = edgeStruct(e).n1;
    v = edgeStruct(e).n2;
    % row = comx (Y), col = comy (X)
    r1 = round( nodeStruct(u).comx );
    c1 = round( nodeStruct(u).comy );
    r2 = round( nodeStruct(v).comx );
    c2 = round( nodeStruct(v).comy );

    % Bresenham’s line algorithm
    dx = abs(c2 - c1);
    dy = abs(r2 - r1);
    sx = sign(c2 - c1);
    sy = sign(r2 - r1);
    err = dx - dy;
    x = c1;
    y = r1;
    lineCols = [];
    lineRows = [];
    while true
        lineCols(end+1) = x;
        lineRows(end+1) = y;
        if x==c2 && y==r2
            break;
        end
        e2 = 2*err;
        if e2 > -dy
            err = err - dy;
            x   = x + sx;
        end
        if e2 < dx
            err = err + dx;
            y   = y + sy;
        end
    end

    idxLine = sub2ind(size(graphImg), lineRows, lineCols );
    graphImg(idxLine) = true;
end

branchPoints = detectBranchpoints(graphImg, false);
end