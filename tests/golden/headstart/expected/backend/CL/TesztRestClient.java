package hu.company.features.teszt.cl;

import hu.company.features.teszt.cl.TesztDtos.AitCriteria;
import hu.company.features.teszt.cl.TesztDtos.AitRow;
import hu.company.features.teszt.cl.TesztDtos.CommitRequest;
import hu.company.features.teszt.cl.TesztDtos.CommitResult;
import hu.company.features.teszt.cl.TesztDtos.LovRequest;
import hu.company.features.teszt.cl.TesztDtos.LovResult;
import hu.company.features.teszt.cl.TesztDtos.PageResult;
import hu.company.features.teszt.cl.TesztDtos.RowResult;
import hu.company.features.teszt.cl.TesztDtos.SearchRequest;

public interface TesztRestClient {
    RowResult<AitRow> updateAit(AitRow original, AitRow value);

    PageResult<AitRow> searchAit(SearchRequest<AitCriteria> request);

    LovResult lovInptip(LovRequest request);

    CommitResult commitForm(CommitRequest request);
}
