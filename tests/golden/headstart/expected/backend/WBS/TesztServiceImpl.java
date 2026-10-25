package hu.company.features.teszt.wbs;

import java.net.URI;

import hu.company.features.teszt.cl.TesztConstants;
import hu.company.features.teszt.cl.TesztDtos.AitCriteria;
import hu.company.features.teszt.cl.TesztDtos.AitRow;
import hu.company.features.teszt.cl.TesztDtos.CommitRequest;
import hu.company.features.teszt.cl.TesztDtos.CommitResult;
import hu.company.features.teszt.cl.TesztDtos.LovRequest;
import hu.company.features.teszt.cl.TesztDtos.LovResult;
import hu.company.features.teszt.cl.TesztDtos.PageResult;
import hu.company.features.teszt.cl.TesztDtos.RowResult;
import hu.company.features.teszt.cl.TesztDtos.SearchRequest;
import hu.company.features.teszt.cl.TesztRestClient;
import hu.company.features.teszt.cl.TesztRestClientImpl;
import lombok.extern.slf4j.XSlf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.stereotype.Service;

/** CREATE_ONCE: host adapter. No database or DPS class dependencies. Every method runs in log1x. */
@XSlf4j
@Service
public class TesztServiceImpl extends ModuleServiceBase<WbsLogHelper> implements TesztService {
    private static final String GENERATED_DPS_URL = "";
    private final TesztRestClient client;

    public TesztServiceImpl(RestTemplateBuilder builder,
            @Value("${frm.teszt.dps-base-url:}") String configuredUrl) {
        String url = configuredUrl == null || configuredUrl.isBlank() ? GENERATED_DPS_URL : configuredUrl.trim();
        URI uri = URI.create(url);
        if (!("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())) || uri.getHost() == null
                || uri.getRawUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null) {
            throw new IllegalArgumentException("Állítsd be: frm.teszt.dps-base-url");
        }
        this.client = new TesztRestClientImpl(builder.build(), url.replaceAll("/+$", ""));
    }

    @Override
    public RowResult<AitRow> updateAit(UserDto user, AitRow original, AitRow value) throws Exception {
        return log1x(log, TesztConstants.UPDATE_AIT_NAME, user, null, () -> client.updateAit(original, value));
    }

    @Override
    public PageResult<AitRow> searchAit(UserDto user, SearchRequest<AitCriteria> request) throws Exception {
        return log1x(log, TesztConstants.SEARCH_AIT_NAME, user, null, () -> client.searchAit(request));
    }

    @Override
    public LovResult lovInptip(UserDto user, LovRequest request) throws Exception {
        return log1x(log, TesztConstants.LOV_INPTIP_NAME, user, null, () -> client.lovInptip(request));
    }

    @Override
    public CommitResult commitForm(UserDto user, CommitRequest request) throws Exception {
        return log1x(log, TesztConstants.COMMIT_FORM_NAME, user, null, () -> client.commitForm(request));
    }
}
