package hu.company.features.teszt.dps;

import java.util.Objects;

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
import hu.company.features.teszt.cl.TesztDtos.UpdateRequest;
import lombok.extern.slf4j.XSlf4j;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** CREATE_ONCE: --regenerate preserves host customizations. Every endpoint runs in log1x. */
@XSlf4j
@RestController("hu.company.features.teszt.dps.TesztController")
@RequestMapping(TesztConstants.BASE_PATH)
public class TesztControllerImpl extends ModuleControllerBase<DpsLogHelper> implements TesztController {
    private final TesztService service;

    public TesztControllerImpl(TesztService service) {
        this.service = Objects.requireNonNull(service);
    }

    @Override
    public RowResult<AitRow> updateAit(UpdateRequest<AitRow> request) throws Exception {
        UserDto user = getUser();
        return log1x(log, TesztConstants.UPDATE_AIT_NAME, user, null, () -> {
            if (request == null) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
            }
            return service.updateAit(user, request.original(), request.value());
        });
    }

    @Override
    public PageResult<AitRow> searchAit(SearchRequest<AitCriteria> request) throws Exception {
        UserDto user = getUser();
        return log1x(log, TesztConstants.SEARCH_AIT_NAME, user, null, () -> service.searchAit(user, request));
    }

    @Override
    public LovResult lovInptip(LovRequest request) throws Exception {
        UserDto user = getUser();
        return log1x(log, TesztConstants.LOV_INPTIP_NAME, user, null, () -> service.lovInptip(user, request));
    }

    @Override
    public CommitResult commitForm(CommitRequest request) throws Exception {
        UserDto user = getUser();
        return log1x(log, TesztConstants.COMMIT_FORM_NAME, user, null, () -> service.commitForm(user, request));
    }
}
