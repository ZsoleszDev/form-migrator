package hu.company.features.dbcall.wbs;

import java.util.List;

import hu.company.features.dbcall.cl.DbcallDtos.BRow;
import hu.company.features.dbcall.cl.DbcallDtos.CommitRequest;
import hu.company.features.dbcall.cl.DbcallDtos.CommitResult;
import hu.company.features.dbcall.cl.DbcallDtos.PageResult;
import hu.company.features.dbcall.cl.DbcallDtos.RowResult;

public interface DbcallService {
    PageResult<BRow> listB(UserDto user, int offset, int limit) throws Exception;

    RowResult<BRow> createB(UserDto user, BRow row) throws Exception;

    RowResult<BRow> updateB(UserDto user, BRow original, BRow value) throws Exception;

    List<String> deleteB(UserDto user, BRow original) throws Exception;

    CommitResult commitForm(UserDto user, CommitRequest request) throws Exception;
}
