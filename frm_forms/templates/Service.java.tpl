package @@PACKAGE@@;

import @@CL_PACKAGE@@.*;
import @@CL_PACKAGE@@.@@CLASS@@Dtos.*;

import java.util.List;
import jakarta.validation.Validator;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

@Service(@@BEAN_SERVICE@@)
public class @@CLASS@@ServiceImpl implements @@CLASS@@Service {
    private final @@CLASS@@Repository repository;
    private final Validator validator;
    private final MigrationAccess access;
    private final @@CLASS@@Rules rules = new @@CLASS@@Rules();

    public @@CLASS@@ServiceImpl(@@CLASS@@Repository repository, Validator validator, MigrationAccess access) {
        this.repository = repository; this.validator = validator; this.access = access;
    }


    @Transactional(readOnly = true)
    public PageResult list(int offset, int limit) {
        guard("read");
        if (offset < 0 || offset > 1000000 || limit < 1 || limit > 200)
            throw bad("offset: 0..1000000, limit: 1..200 szükséges.");
        var rows = repository.list(offset, limit);
        var context = new RuleContext();
        for (var row : rows) rules.postQuery(row, context);
        return new PageResult(rows, context.messages());
    }

    @Transactional
    public RowResult create(@@CLASS@@Row row) {
        guard("create");
        requireRow(row);
        normalize(row);
@@CREATE_CHECKS@@
        var context = new RuleContext();
        rules.validate(row, context);
        rules.preInsert(row, context);
        normalize(row);
        validate(row);
        repository.insert(row);
        var saved = repository.load(row, false);
        rules.postQuery(saved, context);
        return new RowResult(saved, context.messages());
    }

    @Transactional
    public RowResult update(@@CLASS@@Row original, @@CLASS@@Row row) {
        guard("update");
        requireRow(original); requireRow(row);
        normalize(original); normalize(row);
        var current = repository.load(original, true);
        assertUnchanged(original, current);
@@UPDATE_CHECKS@@
        var context = new RuleContext();
        rules.validate(row, context);
        rules.preUpdate(row, context);
        normalize(row);
        validate(row);
@@KEY_IMMUTABLE@@
        repository.update(row);
        var saved = repository.load(row, false);
        rules.postQuery(saved, context);
        return new RowResult(saved, context.messages());
    }

    @Transactional
    public List<String> delete(@@CLASS@@Row original) {
        guard("delete");
        requireRow(original); normalize(original);
        var current = repository.load(original, true);
        assertUnchanged(original, current);
        var context = new RuleContext();
        rules.preDelete(current, context);
        repository.delete(original);
        return context.messages();
    }

    private void guard(String operation) {
        access.check(@@BLOCK_NAME@@, operation);
        boolean enabled = switch (operation) {
            case "read" -> @@CAN_READ@@;
            case "create" -> @@CAN_CREATE@@;
            case "update" -> @@CAN_UPDATE@@;
            case "delete" -> @@CAN_DELETE@@;
            default -> false;
        };
        if (!enabled) throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a művelet ebben a modulban még nem érhető el.");
    }

    private void normalize(@@CLASS@@Row row) {
@@NORMALIZE@@
    }

    private void validate(@@CLASS@@Row row) {
        var errors = validator.validate(row);
        if (!errors.isEmpty()) throw bad(errors.stream().map(e -> e.getPropertyPath() + ": " + e.getMessage()).sorted().reduce((a, b) -> a + "; " + b).orElse("Hibás rekord"));
@@DOMAIN_CHECKS@@
    }

    private void assertUnchanged(@@CLASS@@Row original, @@CLASS@@Row current) {
        if (@@SNAPSHOT_DIFF@@) throw new ResponseStatusException(HttpStatus.CONFLICT, "A rekord közben megváltozott. Töltsd újra a listát.");
    }

    private void requireRow(@@CLASS@@Row row) { if (row == null) throw bad("Hiányzó rekord."); }
    private ResponseStatusException bad(String message) { return new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, message); }
}
