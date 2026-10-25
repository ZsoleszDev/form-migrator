package hu.company.features.teszt.dps;

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
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;

public interface TesztController {
    @PutMapping(TesztConstants.AIT_UPDATE_PATH)
    RowResult<AitRow> updateAit(@RequestBody UpdateRequest<AitRow> request) throws Exception;

    @PostMapping(TesztConstants.AIT_SEARCH_PATH)
    PageResult<AitRow> searchAit(@RequestBody SearchRequest<AitCriteria> request) throws Exception;

    @PostMapping(TesztConstants.LOV_INPTIP_PATH)
    LovResult lovInptip(@RequestBody LovRequest request) throws Exception;

    @PostMapping(TesztConstants.COMMIT_FORM_PATH)
    CommitResult commitForm(@RequestBody CommitRequest request) throws Exception;
}
