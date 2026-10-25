package hu.company.features.dbcall.dps;

import java.util.List;

import hu.company.features.dbcall.cl.DbcallConstants;
import hu.company.features.dbcall.cl.DbcallDtos.BRow;
import hu.company.features.dbcall.cl.DbcallDtos.CommitRequest;
import hu.company.features.dbcall.cl.DbcallDtos.CommitResult;
import hu.company.features.dbcall.cl.DbcallDtos.PageResult;
import hu.company.features.dbcall.cl.DbcallDtos.RowResult;
import hu.company.features.dbcall.cl.DbcallDtos.UpdateRequest;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;

public interface DbcallController {
    @GetMapping(DbcallConstants.B_LIST_PATH)
    PageResult<BRow> listB(@RequestParam(name = "offset", defaultValue = "0") int offset, @RequestParam(name = "limit", defaultValue = "50") int limit) throws Exception;

    @PostMapping(DbcallConstants.B_CREATE_PATH)
    @ResponseStatus(HttpStatus.CREATED)
    RowResult<BRow> createB(@RequestBody BRow row) throws Exception;

    @PutMapping(DbcallConstants.B_UPDATE_PATH)
    RowResult<BRow> updateB(@RequestBody UpdateRequest<BRow> request) throws Exception;

    @DeleteMapping(DbcallConstants.B_DELETE_PATH)
    List<String> deleteB(@RequestBody BRow original) throws Exception;

    @PostMapping(DbcallConstants.COMMIT_FORM_PATH)
    CommitResult commitForm(@RequestBody CommitRequest request) throws Exception;
}
