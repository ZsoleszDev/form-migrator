package @@PACKAGE@@;

/** Implement as a Spring bean using the host application's existing authorization.
 * Throw AccessDeniedException/ResponseStatusException on denial.
 * No permissive default implementation is generated.
 * Row-level/tenant filters must ALSO be integrated in the repository before use.
 */
public interface MigrationAccess {
    void check(String oracleBlock, String operation);
}
