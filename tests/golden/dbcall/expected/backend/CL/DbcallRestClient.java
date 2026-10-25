package hu.company.features.dbcall.cl;

import java.util.List;

import hu.company.features.dbcall.cl.DbcallDtos.BRow;
import hu.company.features.dbcall.cl.DbcallDtos.CommitRequest;
import hu.company.features.dbcall.cl.DbcallDtos.CommitResult;
import hu.company.features.dbcall.cl.DbcallDtos.PageResult;
import hu.company.features.dbcall.cl.DbcallDtos.RowResult;

public interface DbcallRestClient {
    PageResult<BRow> listB(int offset, int limit);

    RowResult<BRow> createB(BRow row);

    RowResult<BRow> updateB(BRow original, BRow value);

    List<String> deleteB(BRow original);

    CommitResult commitForm(CommitRequest request);
}
