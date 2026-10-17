function verify_reference(inputPath, outputPath)
% Independent breakpoint quadrature; rectangular windows, linear mW/Hz.
% No Python implementation is called and no 5G Toolbox license is needed.
data = jsondecode(fileread(inputPath));
reports = struct([]);
for c = 1:numel(data.cases)
    item = data.cases(c);
    edges = item.spectrum.edges_hz(:);
    density = item.spectrum.psd_mw_hz(:);
    windows = item.windows;
    power = zeros(size(windows,1),1);
    for j = 1:size(windows,1)
        lo = windows(j,1); hi = windows(j,2);
        knots = [lo; edges(edges > lo & edges < hi); hi];
        mids = (knots(1:end-1) + knots(2:end))/2;
        [~,~,idx] = histcounts(mids,edges);
        assert(all(idx > 0));
        power(j) = sum(diff(knots).*density(idx));
        tones = item.spectrum.tones;
        if ~isempty(tones)
            include = tones(:,1) >= lo & tones(:,1) < hi;
            power(j) = power(j) + sum(tones(include,2));
        end
    end
    reference = item.python_power_mw(:);
    delta = abs(10*log10(power./reference));
    assert(all(isfinite(delta)) && max(delta) <= 0.10, 'Reference disagreement');
    reports(c).name = item.name;
    reports(c).windows = numel(power);
    reports(c).max_error_db = max(delta);
    reports(c).matlab_power_mw = power;
end
result.matlab_version = version;
result.fixture_type = 'analytical piecewise PSD and tones, not NR waveforms';
result.tolerance_db = 0.10;
result.passed = true;
result.cases = reports;
fid = fopen(outputPath,'w');
assert(fid >= 0); cleanup = onCleanup(@() fclose(fid));
fprintf(fid,'%s',jsonencode(result,PrettyPrint=true));
for c = 1:numel(reports)
    fprintf('%s: %d windows, max difference %.12g dB\n', reports(c).name, reports(c).windows, reports(c).max_error_db);
end
end
