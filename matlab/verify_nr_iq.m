function verify_nr_iq(outputDir)
% Independent gated reference from saved IQ in sqrt(mW).
if nargin<1
    outputDir=fullfile(fileparts(fileparts(mfilename('fullpath'))),'outputs','nr_reference');
end
models={'NR-FR1-TM1.1','NR-FR1-TM1.2'};
for k=1:numel(models)
    stem=strrep(models{k},'.','_'); path=fullfile(outputDir,[stem '_reference.json']);
    data=jsondecode(fileread(path));
    fid=fopen(fullfile(outputDir,[stem '_iq_f32.bin']),'r','ieee-le');
    assert(fid>=0); raw=fread(fid,[2 Inf],'single=>double'); fclose(fid);
    assert(size(raw,2)==data.samples && all(isfinite(raw(:))));
    fs=data.sample_rate_hz; nslot=round(fs*0.0005);
    x=complex(raw(1,1:nslot),raw(2,1:nslot)).';
    nfft=16384; blocks=floor(numel(x)/nfft);
    w=.5-.5*cos(2*pi*(0:nfft-1)'/nfft);
    segments=reshape(x(1:blocks*nfft),nfft,blocks);
    density=mean(abs(fftshift(fft(segments.*w,nfft,1),1)).^2,2)/(fs*sum(w.^2));
    df=fs/nfft; f=(-nfft/2:nfft/2-1)'*df+3550e6;
    edges=[f-df/2;f(end)+df/2]; windows=[]; powers=[]; sides={}; offsets=[];
    for side=1:2
        for off=50000:100000:9950000
            if side==1, center=3500e6-off; sideName='lower';
            else, center=3600e6+off; sideName='upper'; end
            lo=center-50000; hi=center+50000;
            assert(lo>=edges(1) && hi<=edges(end));
            overlap=max(0,min(edges(2:end),hi)-max(edges(1:end-1),lo));
            windows(end+1,:)=[lo hi]; powers(end+1,1)=sum(overlap.*density); %#ok<AGROW>
            sides{end+1,1}=sideName; offsets(end+1,1)=off; %#ok<AGROW>
        end
    end
    data.carrier_center_hz=3550e6; data.matlab_version=version;
    data.gate=struct('start_sample',0,'stop_sample_exclusive',nslot,'description','First 0.5 ms downlink slot');
    data.fft_length=nfft; data.discarded_tail_samples=nslot-blocks*nfft;
    data.psd_method='Periodic Hann; nonoverlapping FFT blocks inside the first DL slot; two-sided mW/Hz';
    data.first_slot_power_dbm=10*log10(mean(abs(x).^2));
    data.spectrum.edges_hz=edges; data.spectrum.psd_mw_hz=density; data.spectrum.tones=[];
    data.windows=windows; data.matlab_power_mw=powers; data.sides=sides; data.offsets_hz=offsets;
    data.coverage_note='Reduced near-channel grid only: 100 kHz filters within 10 MHz of each channel edge. No full-span claim.';
    fid=fopen(path,'w'); assert(fid>=0); fprintf(fid,'%s',jsonencode(data)); fclose(fid);
    fprintf('%s: %d near-channel windows; first DL slot %.6f dBm\n',models{k},size(windows,1),data.first_slot_power_dbm);
end
end
