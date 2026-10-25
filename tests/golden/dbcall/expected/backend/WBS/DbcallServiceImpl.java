package hu.company.features.dbcall.wbs;

import java.net.URI;
import java.util.List;

import hu.company.features.dbcall.cl.DbcallConstants;
import hu.company.features.dbcall.cl.DbcallDtos.BRow;
import hu.company.features.dbcall.cl.DbcallDtos.CommitRequest;
import hu.company.features.dbcall.cl.DbcallDtos.CommitResult;
import hu.company.features.dbcall.cl.DbcallDtos.PageResult;
import hu.company.features.dbcall.cl.DbcallDtos.RowResult;
import hu.company.features.dbcall.cl.DbcallRestClient;
import hu.company.features.dbcall.cl.DbcallRestClientImpl;
import lombok.extern.slf4j.XSlf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.stereotype.Service;

/** CREATE_ONCE: host adapter. No database or DPS class dependencies. Every method runs in log1x. */
@XSlf4j
@Service
public class DbcallServiceImpl extends ModuleServiceBase<WbsLogHelper> implements DbcallService {
    private static final String GENERATED_DPS_URL = "";
    private final DbcallRestClient client;

    public DbcallServiceImpl(RestTemplateBuilder builder,
            @Value("${frm.dbcall.dps-base-url:}") String configuredUrl) {
        String url = configuredUrl == null || configuredUrl.isBlank() ? GENERATED_DPS_URL : configuredUrl.trim();
        URI uri = URI.create(url);
        if (!("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())) || uri.getHost() == null
                || uri.getRawUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null) {
            throw new IllegalArgumentException("Állítsd be: frm.dbcall.dps-base-url");
        }
        this.client = new DbcallRestClientImpl(builder.build(), url.replaceAll("/+$", ""));
    }

    @Override
    public PageResult<BRow> listB(UserDto user, int offset, int limit) throws Exception {
        return log1x(log, DbcallConstants.LIST_B_NAME, user, null, () -> client.listB(offset, limit));
    }

    @Override
    public RowResult<BRow> createB(UserDto user, BRow row) throws Exception {
        return log1x(log, DbcallConstants.CREATE_B_NAME, user, null, () -> client.createB(row));
    }

    @Override
    public RowResult<BRow> updateB(UserDto user, BRow original, BRow value) throws Exception {
        return log1x(log, DbcallConstants.UPDATE_B_NAME, user, null, () -> client.updateB(original, value));
    }

    @Override
    public List<String> deleteB(UserDto user, BRow original) throws Exception {
        return log1x(log, DbcallConstants.DELETE_B_NAME, user, null, () -> client.deleteB(original));
    }

    @Override
    public CommitResult commitForm(UserDto user, CommitRequest request) throws Exception {
        return log1x(log, DbcallConstants.COMMIT_FORM_NAME, user, null, () -> client.commitForm(request));
    }
}
