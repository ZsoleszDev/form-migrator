package hu.company.features.dbcall.cl;

import java.util.List;
import java.util.Objects;

import hu.company.features.dbcall.cl.DbcallDtos.BRow;
import hu.company.features.dbcall.cl.DbcallDtos.CommitRequest;
import hu.company.features.dbcall.cl.DbcallDtos.CommitResult;
import hu.company.features.dbcall.cl.DbcallDtos.PageResult;
import hu.company.features.dbcall.cl.DbcallDtos.RowResult;
import hu.company.features.dbcall.cl.DbcallDtos.UpdateRequest;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.server.ResponseStatusException;

/** RestTemplate from the WBS host (RestTemplateBuilder): authentication, interceptors and timeouts. */
public class DbcallRestClientImpl implements DbcallRestClient {
    private final RestTemplate rest;
    private final String baseUrl;

    public DbcallRestClientImpl(RestTemplate rest, String baseUrl) {
        this.rest = Objects.requireNonNull(rest);
        this.baseUrl = Objects.requireNonNull(baseUrl);
    }

    @Override
    public PageResult<BRow> listB(int offset, int limit) {
        return call(HttpMethod.GET, DbcallConstants.B_LIST_PATH + "?offset={offset}&limit={limit}", null, new ParameterizedTypeReference<PageResult<BRow>>() {}, offset, limit);
    }

    @Override
    public RowResult<BRow> createB(BRow row) {
        return call(HttpMethod.POST, DbcallConstants.B_CREATE_PATH, row, new ParameterizedTypeReference<RowResult<BRow>>() {});
    }

    @Override
    public RowResult<BRow> updateB(BRow original, BRow value) {
        return call(HttpMethod.PUT, DbcallConstants.B_UPDATE_PATH, new UpdateRequest<>(original, value), new ParameterizedTypeReference<RowResult<BRow>>() {});
    }

    @Override
    public List<String> deleteB(BRow original) {
        return call(HttpMethod.DELETE, DbcallConstants.B_DELETE_PATH, original, new ParameterizedTypeReference<List<String>>() {});
    }

    @Override
    public CommitResult commitForm(CommitRequest request) {
        return call(HttpMethod.POST, DbcallConstants.COMMIT_FORM_PATH, request, new ParameterizedTypeReference<CommitResult>() {});
    }

    private <T> T call(HttpMethod method, String path, Object body, ParameterizedTypeReference<T> type, Object... variables) {
        try {
            HttpEntity<Object> entity = body == null ? null : new HttpEntity<>(body);
            T result = rest.exchange(baseUrl + DbcallConstants.BASE_PATH + path, method, entity, type, variables).getBody();
            if (result == null) {
                throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "Üres DPS-válasz.");
            }
            return result;
        } catch (RestClientResponseException error) {
            HttpStatus status = HttpStatus.resolve(error.getRawStatusCode());
            throw new ResponseStatusException(status == null ? HttpStatus.BAD_GATEWAY : status, "A DPS elutasította "
                    + "a kérést.", error);
        } catch (RestClientException error) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "A DPS nem érhető el.", error);
        }
    }
}
