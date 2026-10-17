function export_nr_references(outputDir)
% Generate distinct NR IQ records and independent first-DL-slot references.
if ~isfolder(outputDir), mkdir(outputDir); end
models={'NR-FR1-TM1.1','NR-FR1-TM1.2'};
for k=1:numel(models)
    % This seed controls waveform generation, unlike removed measurement seeds.
    rng(20260919,'twister');
    g=wirelessWaveformGenerator.internal.hNRReferenceWaveformGenerator(models{k},'100MHz','30kHz','TDD');
    [wave,info]=generateWaveform(g); fs=info.Info.SampleRate;
    x=wave(:,1); nslot=round(fs*0.0005);
    x=x*sqrt(10^(46/10)/mean(abs(x(1:nslot)).^2));
    stem=strrep(models{k},'.','_');
    fid=fopen(fullfile(outputDir,[stem '_iq_f32.bin']),'w','ieee-le');
    assert(fid>=0); fwrite(fid,[real(x).';imag(x).'],'single'); fclose(fid);
    data=struct('model',models{k},'sample_rate_hz',fs,'samples',numel(x), ...
        'duration_s',numel(x)/fs,'channel_bandwidth_hz',100e6,'scs_hz',30000, ...
        'duplex','TDD','iq_format','Little-endian interleaved float32 I,Q in sqrt(mW)', ...
        'generation_seed',20260919);
    data.configured_model=cellstr(string(g.ConfiguredModel));
    fid=fopen(fullfile(outputDir,[stem '_reference.json']),'w'); assert(fid>=0);
    fprintf(fid,'%s',jsonencode(data)); fclose(fid);
    save(fullfile(outputDir,[stem '_configuration.mat']),'g','-v7');
end
verify_nr_iq(outputDir);
end
