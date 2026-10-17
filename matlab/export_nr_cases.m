function export_nr_cases(baseDir, outputDir)
% Add two controlled tones to first-DL-slot IQ; preserve the original records.
root=fileparts(fileparts(mfilename('fullpath')));
if nargin<1, baseDir=fullfile(root,'outputs','nr_reference'); end
if nargin<2, outputDir=fullfile(root,'outputs','nr_cases'); end
spec=jsondecode(fileread(fullfile(root,'requirements','nr_benchmark.json')));
for c=1:numel(spec.cases)
    item=spec.cases(c); folder=fullfile(outputDir,item.id);
    if ~isfolder(folder), mkdir(folder); end
    additions=struct([]);
    for m=1:numel(spec.models)
        model=spec.models{m}; stem=strrep(model,'.','_');
        data=jsondecode(fileread(fullfile(baseDir,[stem '_reference.json'])));
        assert(data.sample_rate_hz==spec.sample_rate_hz);
        fid=fopen(fullfile(baseDir,[stem '_iq_f32.bin']),'r','ieee-le'); assert(fid>=0);
        raw=fread(fid,[2 Inf],'single=>double'); fclose(fid);
        fs=data.sample_rate_hz; n=round(fs*spec.gate_duration_s);
        assert(data.gate.start_sample==0 && data.gate.stop_sample_exclusive==n);
        x=complex(raw(1,1:n),raw(2,1:n)).';
        target=10^((spec.tone_filter_limit_dbm-item.target_margin_db)/10);
        amps=zeros(numel(spec.tone_frequencies_hz),1);
        % Calibrate integrated total power, including coherent cross terms.
        for sweep=1:3
            for j=1:numel(amps)
                f=spec.tone_frequencies_hz(j);
                tone=exp(1j*2*pi*(f-3550e6)*(0:n-1)'/fs);
                x=x-amps(j)*tone;
                p0=filter_power(x,fs,f);
                p1=filter_power(x+tone,fs,f);
                a=filter_power(tone,fs,f); b=p1-p0-a;
                assert(target>p0,'Baseline already exceeds target; choose another offset.');
                amps(j)=(-b+sqrt(b*b+4*a*(target-p0)))/(2*a);
                x=x+amps(j)*tone;
            end
        end
        % No renormalization after injection: source power change is recorded.
        fid=fopen(fullfile(folder,[stem '_iq_f32.bin']),'w','ieee-le'); assert(fid>=0);
        fwrite(fid,[real(x).';imag(x).'],'single'); fclose(fid);
        data.samples=n; data.duration_s=n/fs;
        fid=fopen(fullfile(folder,[stem '_reference.json']),'w'); assert(fid>=0);
        fprintf(fid,'%s',jsonencode(data)); fclose(fid);
        additions(m).model=model;
        additions(m).tone_amplitude_sqrt_mw=amps;
        additions(m).composite_gate_power_dbm=10*log10(mean(abs(x).^2));
    end
    verify_nr_iq(folder);
    margins=[]; maximum=-Inf;
    for m=1:numel(spec.models)
        stem=strrep(spec.models{m},'.','_');
        d=jsondecode(fileread(fullfile(folder,[stem '_reference.json'])));
        levels=10*log10(d.matlab_power_mw(:)); offsets=d.offsets_hz(:)/1e6;
        limits=-14*ones(size(offsets)); selected=offsets<5.05;
        limits(selected)=-7-1.4*(offsets(selected)-.05);
        margins=[margins;limits-levels]; %#ok<AGROW>
        centers=mean(d.windows,2);
        for f=spec.tone_frequencies_hz(:).'
            ix=find(abs(centers-f)<1); assert(numel(ix)==1);
            assert(abs(limits(ix)-levels(ix)-item.target_margin_db)<=spec.target_tolerance_db);
        end
        maximum=max(maximum,max(levels-limits));
    end
    if maximum>0, decision='fail'; else, decision='pass'; end
    assert(strcmp(decision,item.expected_decision));
    manifest=struct('id',item.id,'target_margin_db',item.target_margin_db, ...
        'actual_minimum_margin_db',min(margins),'decision',decision, ...
        'tone_frequencies_hz',spec.tone_frequencies_hz,'models',additions);
    fid=fopen(fullfile(folder,'case.json'),'w');assert(fid>=0);
    fprintf(fid,'%s',jsonencode(manifest,PrettyPrint=true));fclose(fid);
    fprintf('%s: %s, minimum margin %.4f dB\n',item.id,decision,min(margins));
end
end

function power=filter_power(x,fs,center)
nfft=16384; blocks=floor(numel(x)/nfft);
w=.5-.5*cos(2*pi*(0:nfft-1)'/nfft);
transformed=fftshift(fft(reshape(x(1:blocks*nfft),nfft,blocks).*w,nfft,1),1);
density=mean(abs(transformed).^2,2)/(fs*sum(w.^2));
df=fs/nfft; frequency=(-nfft/2:nfft/2-1)'*df+3550e6;
overlap=max(0,min(frequency+df/2,center+50000)-max(frequency-df/2,center-50000));
power=sum(overlap.*density);
end
