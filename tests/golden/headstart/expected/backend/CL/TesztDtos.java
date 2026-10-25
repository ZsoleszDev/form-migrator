package hu.company.features.teszt.cl;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonFormat;

/** Only endpoint DTOs. Oracle NUMBER stays a decimal string on the wire. */
public final class TesztDtos {
    private TesztDtos() {
    }

    public static class RowResult<T> {
        public T row;
        public List<String> messages;

        public RowResult() {
        }

        public RowResult(T row, List<String> messages) {
            this.row = row;
            this.messages = messages;
        }

        public T row() {
            return row;
        }

        public List<String> messages() {
            return messages;
        }
    }

    public static class PageResult<T> {
        public List<T> rows;
        public List<String> messages;

        public PageResult() {
        }

        public PageResult(List<T> rows, List<String> messages) {
            this.rows = rows;
            this.messages = messages;
        }

        public List<T> rows() {
            return rows;
        }

        public List<String> messages() {
            return messages;
        }
    }

    public static class UpdateRequest<T> {
        public T original;
        public T value;

        public UpdateRequest() {
        }

        public UpdateRequest(T original, T value) {
            this.original = original;
            this.value = value;
        }

        public T original() {
            return original;
        }

        public T value() {
            return value;
        }
    }

    public static class SearchRequest<T> {
        public T criteria;
        public int offset;
        public int limit;

        public SearchRequest() {
        }

        public SearchRequest(T criteria, int offset, int limit) {
            this.criteria = criteria;
            this.offset = offset;
            this.limit = limit;
        }

        public T criteria() {
            return criteria;
        }

        public int offset() {
            return offset;
        }

        public int limit() {
            return limit;
        }
    }

    // Forms COMMIT_FORM: blokkonként az új, módosított és törölt rekordok; blocks/parameters = a képernyő értékei.
    public static class BlockChanges<T> {
        public List<T> inserted;
        public List<UpdateRequest<T>> updated;
        public List<T> deleted;

        public BlockChanges() {
        }

        public BlockChanges(List<T> inserted, List<UpdateRequest<T>> updated, List<T> deleted) {
            this.inserted = inserted;
            this.updated = updated;
            this.deleted = deleted;
        }

        public List<T> inserted() {
            return inserted;
        }

        public List<UpdateRequest<T>> updated() {
            return updated;
        }

        public List<T> deleted() {
            return deleted;
        }
    }

    public static class CommitRequest {
        public Map<String, Map<String, String>> blocks;
        public Map<String, String> parameters;
        public BlockChanges<AitRow> changesAit;

        public CommitRequest() {
        }

        public CommitRequest(Map<String, Map<String, String>> blocks, Map<String, String> parameters, BlockChanges<AitRow> changesAit) {
            this.blocks = blocks;
            this.parameters = parameters;
            this.changesAit = changesAit;
        }

        public Map<String, Map<String, String>> blocks() {
            return blocks;
        }

        public Map<String, String> parameters() {
            return parameters;
        }

        public BlockChanges<AitRow> changesAit() {
            return changesAit;
        }
    }

    // A mentett rekordok blokkonként (újraolvasva), a triggerek üzenetei, utasításai és :GLOBAL értékei.
    public static class CommitResult {
        public Map<String, Map<String, String>> blocks;
        public List<String> messages;
        public List<List<String>> commands;
        public Map<String, String> globals;
        public List<AitRow> rowsAit;

        public CommitResult() {
        }

        public CommitResult(Map<String, Map<String, String>> blocks, List<String> messages, List<List<String>> commands, Map<String, String> globals, List<AitRow> rowsAit) {
            this.blocks = blocks;
            this.messages = messages;
            this.commands = commands;
            this.globals = globals;
            this.rowsAit = rowsAit;
        }

        public Map<String, Map<String, String>> blocks() {
            return blocks;
        }

        public List<String> messages() {
            return messages;
        }

        public List<List<String>> commands() {
            return commands;
        }

        public Map<String, String> globals() {
            return globals;
        }

        public List<AitRow> rowsAit() {
            return rowsAit;
        }
    }

    // LOV: term = typed text (LIKE 'term%' on the displayed column), parameters = Forms binds by BLOCK.ITEM.
    public static class LovRequest {
        public String term;
        public Map<String, String> parameters;
        public Integer limit;

        public LovRequest() {
        }

        public LovRequest(String term, Map<String, String> parameters, Integer limit) {
            this.term = term;
            this.parameters = parameters;
            this.limit = limit;
        }

        public String term() {
            return term;
        }

        public Map<String, String> parameters() {
            return parameters;
        }

        public Integer limit() {
            return limit;
        }
    }

    public static class LovResult {
        public List<Map<String, Object>> rows;

        public LovResult() {
        }

        public LovResult(List<Map<String, Object>> rows) {
            this.rows = rows;
        }

        public List<Map<String, Object>> rows() {
            return rows;
        }
    }

    // Forms rekord: AIT; adatforrás: ANK_ADLAP_ITEMS
    public static class AitRow {
        @JsonFormat(shape = JsonFormat.Shape.STRING)
        public BigDecimal aitKulcs;
        public String aitTipus;
        public String aitStatus;
        public String aitMegj;
        public String lUres3;
    }

    public static class AitCriteria {
        public String vElekAdlapUbiInptipKod;
    }
}
