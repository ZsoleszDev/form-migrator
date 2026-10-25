package hu.company.features.dbcall.cl;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonFormat;

/** Only endpoint DTOs. Oracle NUMBER stays a decimal string on the wire. */
public final class DbcallDtos {
    private DbcallDtos() {
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
        public BlockChanges<BRow> changesB;

        public CommitRequest() {
        }

        public CommitRequest(Map<String, Map<String, String>> blocks, Map<String, String> parameters, BlockChanges<BRow> changesB) {
            this.blocks = blocks;
            this.parameters = parameters;
            this.changesB = changesB;
        }

        public Map<String, Map<String, String>> blocks() {
            return blocks;
        }

        public Map<String, String> parameters() {
            return parameters;
        }

        public BlockChanges<BRow> changesB() {
            return changesB;
        }
    }

    // A mentett rekordok blokkonként (újraolvasva), a triggerek üzenetei, utasításai és :GLOBAL értékei.
    public static class CommitResult {
        public Map<String, Map<String, String>> blocks;
        public List<String> messages;
        public List<List<String>> commands;
        public Map<String, String> globals;
        public List<BRow> rowsB;

        public CommitResult() {
        }

        public CommitResult(Map<String, Map<String, String>> blocks, List<String> messages, List<List<String>> commands, Map<String, String> globals, List<BRow> rowsB) {
            this.blocks = blocks;
            this.messages = messages;
            this.commands = commands;
            this.globals = globals;
            this.rowsB = rowsB;
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

        public List<BRow> rowsB() {
            return rowsB;
        }
    }

    // Forms rekord: B; adatforrás: T_B
    public static class BRow {
        @JsonFormat(shape = JsonFormat.Shape.STRING)
        public BigDecimal id;
        public String kod;
        public String nev;
        @JsonFormat(pattern = "yyyy-MM-dd'T'HH:mm:ss")
        public LocalDateTime datum;
    }
}
