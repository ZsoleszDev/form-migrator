package hu.company.features.dbcall.dps;

import java.util.List;
import java.util.Objects;

import hu.company.features.dbcall.cl.DbcallConstants;
import hu.company.features.dbcall.cl.DbcallDtos.BRow;
import hu.company.features.dbcall.cl.DbcallDtos.CommitRequest;
import hu.company.features.dbcall.cl.DbcallDtos.CommitResult;
import hu.company.features.dbcall.cl.DbcallDtos.PageResult;
import hu.company.features.dbcall.cl.DbcallDtos.RowResult;
import hu.company.features.dbcall.cl.DbcallDtos.UpdateRequest;
import lombok.extern.slf4j.XSlf4j;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** CREATE_ONCE: --regenerate preserves host customizations. Every endpoint runs in log1x. */
@XSlf4j
@RestController("hu.company.features.dbcall.dps.DbcallController")
@RequestMapping(DbcallConstants.BASE_PATH)
public class DbcallControllerImpl extends ModuleControllerBase<DpsLogHelper> implements DbcallController {
    private final DbcallService service;

    public DbcallControllerImpl(DbcallService service) {
        this.service = Objects.requireNonNull(service);
    }

    @Override
    public PageResult<BRow> listB(int offset, int limit) throws Exception {
        UserDto user = getUser();
        return log1x(log, DbcallConstants.LIST_B_NAME, user, null, () -> service.listB(user, offset, limit));
    }

    @Override
    public RowResult<BRow> createB(BRow row) throws Exception {
        UserDto user = getUser();
        return log1x(log, DbcallConstants.CREATE_B_NAME, user, null, () -> service.createB(user, row));
    }

    @Override
    public RowResult<BRow> updateB(UpdateRequest<BRow> request) throws Exception {
        UserDto user = getUser();
        return log1x(log, DbcallConstants.UPDATE_B_NAME, user, null, () -> {
            if (request == null) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
            }
            return service.updateB(user, request.original(), request.value());
        });
    }

    @Override
    public List<String> deleteB(BRow original) throws Exception {
        UserDto user = getUser();
        return log1x(log, DbcallConstants.DELETE_B_NAME, user, null, () -> service.deleteB(user, original));
    }

    @Override
    public CommitResult commitForm(CommitRequest request) throws Exception {
        UserDto user = getUser();
        return log1x(log, DbcallConstants.COMMIT_FORM_NAME, user, null, () -> service.commitForm(user, request));
    }
}
