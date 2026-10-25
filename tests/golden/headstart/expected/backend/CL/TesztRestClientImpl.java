package hu.company.features.teszt.cl;

import java.util.Objects;

import hu.company.features.teszt.cl.TesztDtos.AitCriteria;
import hu.company.features.teszt.cl.TesztDtos.AitRow;
import hu.company.features.teszt.cl.TesztDtos.CommitRequest;
import hu.company.features.teszt.cl.TesztDtos.CommitResult;
import hu.company.features.teszt.cl.TesztDtos.LovRequest;
import hu.company.features.teszt.cl.TesztDtos.LovResult;
import hu.company.features.teszt.cl.TesztDtos.PageResult;
import hu.company.features.teszt.cl.TesztDtos.RowResult;
import hu.company.features.teszt.cl.TesztDtos.SearchRequest;
import hu.company.features.teszt.cl.TesztDtos.UpdateRequest;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.server.ResponseStatusException;

/** RestTemplate from the WBS host (RestTemplateBuilder): authentication, interceptors and timeouts. */
public class TesztRestClientImpl implements TesztRestClient {
    private final RestTemplate rest;
    private final String baseUrl;

    public TesztRestClientImpl(RestTemplate rest, String baseUrl) {
        this.rest = Objects.requireNonNull(rest);
        this.baseUrl = Objects.requireNonNull(baseUrl);
    }

    @Override
    public RowResult<AitRow> updateAit(AitRow original, AitRow value) {
        return call(HttpMethod.PUT, TesztConstants.AIT_UPDATE_PATH, new UpdateRequest<>(original, value), new ParameterizedTypeReference<RowResult<AitRow>>() {});
    }

    @Override
    public PageResult<AitRow> searchAit(SearchRequest<AitCriteria> request) {
        return call(HttpMethod.POST, TesztConstants.AIT_SEARCH_PATH, request, new ParameterizedTypeReference<PageResult<AitRow>>() {});
    }

    @Override
    public LovResult lovInptip(LovRequest request) {
        return call(HttpMethod.POST, TesztConstants.LOV_INPTIP_PATH, request, new ParameterizedTypeReference<LovResult>() {});
    }

    @Override
    public CommitResult commitForm(CommitRequest request) {
        return call(HttpMethod.POST, TesztConstants.COMMIT_FORM_PATH, request, new ParameterizedTypeReference<CommitResult>() {});
    }

    private <T> T call(HttpMethod method, String path, Object body, ParameterizedTypeReference<T> type, Object... variables) {
        try {
            HttpEntity<Object> entity = body == null ? null : new HttpEntity<>(body);
            T result = rest.exchange(baseUrl + TesztConstants.BASE_PATH + path, method, entity, type, variables).getBody();
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
