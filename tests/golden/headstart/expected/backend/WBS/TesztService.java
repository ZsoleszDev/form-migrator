package hu.company.features.teszt.wbs;

import hu.company.features.teszt.cl.TesztDtos.AitCriteria;
import hu.company.features.teszt.cl.TesztDtos.AitRow;
import hu.company.features.teszt.cl.TesztDtos.CommitRequest;
import hu.company.features.teszt.cl.TesztDtos.CommitResult;
import hu.company.features.teszt.cl.TesztDtos.LovRequest;
import hu.company.features.teszt.cl.TesztDtos.LovResult;
import hu.company.features.teszt.cl.TesztDtos.PageResult;
import hu.company.features.teszt.cl.TesztDtos.RowResult;
import hu.company.features.teszt.cl.TesztDtos.SearchRequest;

public interface TesztService {
    RowResult<AitRow> updateAit(UserDto user, AitRow original, AitRow value) throws Exception;

    PageResult<AitRow> searchAit(UserDto user, SearchRequest<AitCriteria> request) throws Exception;

    LovResult lovInptip(UserDto user, LovRequest request) throws Exception;

    CommitResult commitForm(UserDto user, CommitRequest request) throws Exception;
}
