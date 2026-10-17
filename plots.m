models = {'NR-FR1-TM1.1','NR-FR1-TM1.2'};
cases = {'R01','R02','R03','R04'};
labels = {'R01 (+3 dB)','R02 (+0.2 dB)', ...
          'R03 (-0.2 dB)','R04 (-3 dB)'};
colors = lines(4);

fig = figure('Color','w','Position',[100 100 1100 650]);
t = tiledlayout(2,2,'TileSpacing','compact','Padding','compact');

for m = 1:2
    stem = strrep(models{m},'.','_');

    for side = 1:2
        ax = nexttile(t);
        hold(ax,'on');

        for c = 1:4
            file = fullfile('outputs','nr_cases',cases{c}, ...
                            [stem '_reference.json']);
            d = jsondecode(fileread(file));

            if side == 1
                selected = strcmp(d.sides,'lower');
                sideName = 'Lower side';
            else
                selected = strcmp(d.sides,'upper');
                sideName = 'Upper side';
            end

            f = mean(d.windows(selected,:),2)/1e6;
            p = 10*log10(d.matlab_power_mw(selected));
            offset = d.offsets_hz(selected)/1e6;

            limit = -14*ones(size(offset));
            near = offset < 5.05;
            limit(near) = -7 - 1.4*(offset(near)-0.05);

            [f,order] = sort(f);

            plot(ax,f,p(order),'Color',colors(c,:), ...
                 'LineWidth',1.3,'DisplayName',labels{c});

            if c == 4
                plot(ax,f,limit(order),'k--','LineWidth',1.6, ...
                     'DisplayName','OBUE limit');
            end
        end

        title(ax,[models{m} ' — ' sideName]);
        xlabel(ax,'Frequency (MHz)');
        ylabel(ax,'Transmitter-plane filter power (dBm)');
        grid(ax,'on');
        set(ax,'FontName','Arial','FontSize',10);
        ax.XAxis.Exponent = 0;
    end
end

legend(ax,'Location','best','FontSize',8);

exportgraphics(fig,'NR_controlled_cases.pdf','ContentType','vector');
exportgraphics(fig,'NR_controlled_cases.png','Resolution',300);