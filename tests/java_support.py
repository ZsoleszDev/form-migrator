"""Offline compile fixtures for known Spring/Jakarta/Jackson API surfaces.

These are TEST DOUBLES, never shipped inside generated feature modules.
They do not substitute for a real host-framework or Oracle integration build.
"""
from pathlib import Path
import os

os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')  # tests never read a developer's own java-imports.json
os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json


def trigger_mode(mode: str):
    """setUpModule, tearDownModule of a test module written for one backend_trigger_mode.

    Most engine tests were written for the deterministic trigger translation (4.34 default: plsql); a few check the
    4.35 default (ai) through the module's tasks. The mode is set only while the module's tests run: setting it when
    this file is imported leaked it into every module of a discover run, so a result depended on the import order.
    Usage: setUpModule, tearDownModule = trigger_mode('plsql')
    """
    saved = []

    def set_up():
        saved.append(os.environ.get('FRM_BACKEND_TRIGGER_MODE'))
        os.environ['FRM_BACKEND_TRIGGER_MODE'] = mode

    def tear_down():
        previous = saved.pop()
        if previous is None:
            os.environ.pop('FRM_BACKEND_TRIGGER_MODE', None)
        else:
            os.environ['FRM_BACKEND_TRIGGER_MODE'] = previous
    return set_up, tear_down


# Imports of the company classes above, for generator configs in compile tests.
COMPANY_IMPORTS = ['hu.company.common.ModuleServiceBase', 'hu.company.common.ModuleControllerBase', 'hu.company.common.DpsLogHelper',
                   'hu.company.common.WbsLogHelper', 'hu.company.common.UserDto']


def write_stubs(root: Path) -> list[Path]:
    files = {
        'org.springframework.http.HttpStatusCode': 'public interface HttpStatusCode {}',
        'org.springframework.http.HttpStatus': 'public enum HttpStatus implements HttpStatusCode { BAD_REQUEST, NOT_FOUND, CONFLICT, UNPROCESSABLE_ENTITY, NOT_IMPLEMENTED, CREATED, FORBIDDEN, BAD_GATEWAY; public static HttpStatus resolve(int code) { return null; } }',
        'org.springframework.web.server.ResponseStatusException': 'public class ResponseStatusException extends RuntimeException { public final org.springframework.http.HttpStatusCode status; public ResponseStatusException(org.springframework.http.HttpStatusCode s, String m) { super(m); status=s; } public ResponseStatusException(org.springframework.http.HttpStatusCode s, String m, Throwable c) { super(m, c); status=s; } }',
        'org.springframework.jdbc.core.RowMapper': '@FunctionalInterface public interface RowMapper<T> { T mapRow(java.sql.ResultSet rs, int n) throws java.sql.SQLException; }',
        'org.springframework.jdbc.core.namedparam.SqlParameterSource': 'public interface SqlParameterSource { Object getValue(String key); }',
        'org.springframework.jdbc.datasource.DriverManagerDataSource': 'public class DriverManagerDataSource {}',
        'org.springframework.jdbc.core.namedparam.MapSqlParameterSource': 'public class MapSqlParameterSource implements SqlParameterSource { private final java.util.Map<String,Object> values = new java.util.HashMap<>(); public Object getValue(String key) { return values.get(key); } public MapSqlParameterSource addValue(String k, Object v) { values.put(k,v); return this; } public MapSqlParameterSource addValue(String k, Object v, int type) { return addValue(k,v); } }',
        'org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate': 'public class NamedParameterJdbcTemplate { public NamedParameterJdbcTemplate() {} public NamedParameterJdbcTemplate(Object source) {} public <T> java.util.List<T> query(String s, SqlParameterSource p, org.springframework.jdbc.core.RowMapper<T> m) { return java.util.List.of(); } public int update(String s, SqlParameterSource p) { return 1; } public <T> T queryForObject(String s, SqlParameterSource p, Class<T> c) { return null; } public java.util.List<java.util.Map<String,Object>> queryForList(String s, SqlParameterSource p) { return java.util.List.of(); } public org.springframework.jdbc.core.JdbcTemplate getJdbcTemplate() { return new org.springframework.jdbc.core.JdbcTemplate(); } }',
        'org.springframework.jdbc.core.JdbcTemplate': 'public class JdbcTemplate { public <T> T execute(String sql, CallableStatementCallback<T> action) { return null; } }',
        'org.springframework.jdbc.core.CallableStatementCallback': '@FunctionalInterface public interface CallableStatementCallback<T> { T doInCallableStatement(java.sql.CallableStatement cs) throws java.sql.SQLException, org.springframework.dao.DataAccessException; }',
        'org.springframework.dao.DataAccessException': 'public abstract class DataAccessException extends RuntimeException { public DataAccessException(String m) { super(m); } public Throwable getMostSpecificCause() { return this; } }',
        'jakarta.validation.Path': 'public interface Path {}',
        'jakarta.validation.ConstraintViolation': 'public interface ConstraintViolation<T> { Path getPropertyPath(); String getMessage(); }',
        'jakarta.validation.Validator': 'public interface Validator { <T> java.util.Set<ConstraintViolation<T>> validate(T object, Class<?>... groups); }',
        'jakarta.validation.constraints.NotNull': 'public @interface NotNull {}',
        'jakarta.validation.constraints.Size': 'public @interface Size { int max(); }',
        'jakarta.validation.constraints.Digits': 'public @interface Digits { int integer(); int fraction(); }',
        'com.fasterxml.jackson.annotation.JsonFormat': 'public @interface JsonFormat { public enum Shape { ANY, STRING } Shape shape() default Shape.ANY; String pattern() default ""; }',
        'org.springframework.transaction.annotation.Transactional': 'public @interface Transactional { boolean readOnly() default false; Class<? extends Throwable>[] rollbackFor() default {}; }',
        # JUnit 5 surface of the generated query tests (query_java_tests); the stub really asserts, so a runner can execute them.
        'org.junit.jupiter.api.Test': '@java.lang.annotation.Retention(java.lang.annotation.RetentionPolicy.RUNTIME) public @interface Test {}',
        'org.junit.jupiter.api.Assertions': 'public final class Assertions { public static void assertEquals(Object expected, Object actual, String message) { if (!java.util.Objects.equals(expected, actual)) throw new AssertionError(message + ": expected <" + expected + "> but was <" + actual + ">"); } public static void assertEquals(boolean expected, boolean actual, String message) { assertEquals((Object) expected, (Object) actual, message); } }',
    }
    files.update({
        'org.springframework.http.HttpMethod': 'public enum HttpMethod { GET, POST, PUT, DELETE }',
        'org.springframework.core.ParameterizedTypeReference': 'public abstract class ParameterizedTypeReference<T> {}',
        'org.springframework.web.client.RestClientException': 'public class RestClientException extends RuntimeException {}',
        'org.springframework.web.client.RestClientResponseException': 'public class RestClientResponseException extends RestClientException { public int getRawStatusCode() { return 502; } public String getResponseBodyAsString() { return ""; } }',
        # Spring Framework 5.2 / Spring Boot 2.3 HTTP client surface (RestClient is Spring 6.1+).
        'org.springframework.web.client.RestTemplate': 'public class RestTemplate { public <T> org.springframework.http.ResponseEntity<T> exchange(String url, org.springframework.http.HttpMethod method, org.springframework.http.HttpEntity<?> entity, org.springframework.core.ParameterizedTypeReference<T> type, Object... variables) { return null; } }',
        'org.springframework.http.ResponseEntity': 'public class ResponseEntity<T> { public T getBody() { return null; } }',
        'org.springframework.http.HttpEntity': 'public class HttpEntity<T> { public HttpEntity(T body) {} }',
        'org.springframework.boot.web.client.RestTemplateBuilder': 'public class RestTemplateBuilder { public org.springframework.web.client.RestTemplate build() { return new org.springframework.web.client.RestTemplate(); } }',
        'org.springframework.context.annotation.Bean': 'public @interface Bean { String[] name() default {}; }',
        'org.springframework.context.annotation.Configuration': 'public @interface Configuration { String value() default ""; }',
        'org.springframework.beans.factory.annotation.Value': 'public @interface Value { String value(); }',
        'org.springframework.beans.factory.annotation.Qualifier': 'public @interface Qualifier { String value(); }',
        'org.springframework.beans.factory.annotation.Autowired': 'public @interface Autowired { boolean required() default true; }',
        # A company service of the java-variables.json tests (an @Autowired field of the DPS ServiceImpl).
        'hu.company.pelda.CommonService': 'public class CommonService { public String Details(String key) { return key; } }',
    })
    for annotation in ['Service', 'Repository']:
        files['org.springframework.stereotype.' + annotation] = 'public @interface ' + annotation + ' { String value() default ""; }'
    for annotation in ['RestController', 'RequestBody']:
        files['org.springframework.web.bind.annotation.' + annotation] = 'public @interface ' + annotation + ' { String value() default ""; }'
    for annotation in ['RequestMapping', 'GetMapping', 'PostMapping', 'PutMapping', 'DeleteMapping']:
        files['org.springframework.web.bind.annotation.' + annotation] = 'public @interface ' + annotation + ' { String[] value() default {}; }'
    files['org.springframework.web.bind.annotation.ResponseStatus'] = 'public @interface ResponseStatus { org.springframework.http.HttpStatus value(); }'
    files['org.springframework.web.bind.annotation.RequestParam'] = 'public @interface RequestParam { String name(); String defaultValue() default ""; }'
    # Company conventions (log1x, UserDto, @XSlf4j). Lombok does not run here: the base class provides `log`.
    files['lombok.extern.slf4j.XSlf4j'] = 'public @interface XSlf4j {}'
    files['org.slf4j.ext.XLogger'] = 'public class XLogger {}'
    files['hu.company.common.UserDto'] = 'public class UserDto {}'
    files['hu.company.common.DpsLogHelper'] = 'public class DpsLogHelper {}'
    files['hu.company.common.WbsLogHelper'] = 'public class WbsLogHelper {}'
    log1x = ('protected static final org.slf4j.ext.XLogger log = new org.slf4j.ext.XLogger();'
             ' protected <R> R log1x(org.slf4j.ext.XLogger log, String name, UserDto user, Object params,'
             ' java.util.concurrent.Callable<R> body) throws Exception { return body.call(); }')
    files['hu.company.common.ModuleServiceBase'] = 'public abstract class ModuleServiceBase<H> { ' + log1x + ' }'
    files['hu.company.common.ModuleControllerBase'] = ('public abstract class ModuleControllerBase<H> { ' + log1x
                                                       + ' protected UserDto getUser() { return new UserDto(); } }')
    paths = []
    for fullname, source in files.items():
        package, cls = fullname.rsplit('.', 1)
        path = root.joinpath(*fullname.split('.')).with_suffix('.java')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('package ' + package + ';\n' + source + '\n', encoding='utf-8')
        paths.append(path)
    return paths


# The JPA 2.2 API surface the migrator's repositories use (entities, jpa_sql's Criteria methods, the sequence query):
# the real signatures, generics included, so javac checks the generated code as against javax.persistence-api.
JPA_API = {
    'Entity': 'public @interface Entity { String name() default ""; }',
    'Table': 'public @interface Table { String name(); String schema() default ""; }',
    'Column': 'public @interface Column { String name(); boolean nullable() default true; int length() default 255; '
              'int precision() default 0; int scale() default 0; }',
    'Id': 'public @interface Id {}', 'IdClass': 'public @interface IdClass { Class<?> value(); }',
    'Lob': 'public @interface Lob {}', 'PersistenceContext': 'public @interface PersistenceContext {}',
    'Parameter': 'public interface Parameter<T> {}',
    'PersistenceException': 'public class PersistenceException extends RuntimeException {}',
    'NoResultException': 'public class NoResultException extends PersistenceException {}',
    'NonUniqueResultException': 'public class NonUniqueResultException extends PersistenceException {}',
    'Query': 'public interface Query { Object getSingleResult(); java.util.List getResultList(); int executeUpdate(); '
             'Query setMaxResults(int max); Query setParameter(String name, Object value); <T> Query setParameter(Parameter<T> p, T value); }',
    'TypedQuery': 'public interface TypedQuery<X> extends Query { X getSingleResult(); java.util.List<X> getResultList(); '
                  'TypedQuery<X> setMaxResults(int max); TypedQuery<X> setParameter(String name, Object value); '
                  '<T> TypedQuery<X> setParameter(Parameter<T> p, T value); }',
    'EntityManager': 'public interface EntityManager { <T> T find(Class<T> type, Object id); Query createNativeQuery(String sql); '
                     'void persist(Object entity); <T> T merge(T entity); void remove(Object entity); '
                     'default void flush() {} default boolean contains(Object entity) { return false; } '
                     'javax.persistence.criteria.CriteriaBuilder getCriteriaBuilder(); '
                     '<T> TypedQuery<T> createQuery(javax.persistence.criteria.CriteriaQuery<T> query); '
                     'Query createQuery(javax.persistence.criteria.CriteriaUpdate update); '
                     'Query createQuery(javax.persistence.criteria.CriteriaDelete delete); }',
}
CRITERIA_API = {
    'Selection': 'public interface Selection<X> {}',
    'Expression': 'public interface Expression<T> extends Selection<T> { Predicate in(Object... values); '
                  'Predicate in(Expression<?>... values); }',
    'Predicate': 'public interface Predicate extends Expression<Boolean> {}',
    'Path': 'public interface Path<X> extends Expression<X> { <Y> Path<Y> get(String attributeName); }',
    'From': 'public interface From<Z, X> extends Path<X> {}',
    'Root': 'public interface Root<X> extends From<X, X> {}',
    'Order': 'public interface Order {}',
    'ParameterExpression': 'public interface ParameterExpression<T> extends javax.persistence.Parameter<T>, Expression<T> {}',
    'CommonAbstractCriteria': 'public interface CommonAbstractCriteria {}',
    'AbstractQuery': 'public interface AbstractQuery<T> extends CommonAbstractCriteria { <X> Root<X> from(Class<X> type); '
                     'AbstractQuery<T> where(Expression<Boolean> restriction); AbstractQuery<T> where(Predicate... restrictions); }',
    'CriteriaQuery': 'public interface CriteriaQuery<T> extends AbstractQuery<T> { CriteriaQuery<T> select(Selection<? extends T> s); '
                     'CriteriaQuery<T> where(Expression<Boolean> restriction); CriteriaQuery<T> where(Predicate... restrictions); '
                     'CriteriaQuery<T> orderBy(Order... o); }',
    'CriteriaUpdate': 'public interface CriteriaUpdate<T> extends CommonAbstractCriteria { Root<T> from(Class<T> type); '
                      '<Y, X extends Y> CriteriaUpdate<T> set(Path<Y> attribute, X value); '
                      '<Y> CriteriaUpdate<T> set(Path<Y> attribute, Expression<? extends Y> value); '
                      'CriteriaUpdate<T> where(Expression<Boolean> restriction); CriteriaUpdate<T> where(Predicate... restrictions); }',
    'CriteriaDelete': 'public interface CriteriaDelete<T> extends CommonAbstractCriteria { Root<T> from(Class<T> type); '
                      'CriteriaDelete<T> where(Expression<Boolean> restriction); CriteriaDelete<T> where(Predicate... restrictions); }',
    'CriteriaBuilder': '''public interface CriteriaBuilder {
    <T> CriteriaQuery<T> createQuery(Class<T> type);
    <T> CriteriaUpdate<T> createCriteriaUpdate(Class<T> type);
    <T> CriteriaDelete<T> createCriteriaDelete(Class<T> type);
    <T> ParameterExpression<T> parameter(Class<T> type);
    <T> Expression<T> literal(T value);
    <T> Expression<T> nullLiteral(Class<T> type);
    Predicate and(Expression<Boolean> x, Expression<Boolean> y);
    Predicate and(Predicate... restrictions);
    Predicate or(Expression<Boolean> x, Expression<Boolean> y);
    Predicate or(Predicate... restrictions);
    Predicate not(Expression<Boolean> restriction);
    Predicate isNull(Expression<?> x);
    Predicate isNotNull(Expression<?> x);
    Predicate equal(Expression<?> x, Expression<?> y);
    Predicate equal(Expression<?> x, Object y);
    Predicate notEqual(Expression<?> x, Expression<?> y);
    Predicate notEqual(Expression<?> x, Object y);
    <Y extends Comparable<? super Y>> Predicate greaterThan(Expression<? extends Y> x, Expression<? extends Y> y);
    <Y extends Comparable<? super Y>> Predicate greaterThan(Expression<? extends Y> x, Y y);
    <Y extends Comparable<? super Y>> Predicate greaterThanOrEqualTo(Expression<? extends Y> x, Expression<? extends Y> y);
    <Y extends Comparable<? super Y>> Predicate greaterThanOrEqualTo(Expression<? extends Y> x, Y y);
    <Y extends Comparable<? super Y>> Predicate lessThan(Expression<? extends Y> x, Expression<? extends Y> y);
    <Y extends Comparable<? super Y>> Predicate lessThan(Expression<? extends Y> x, Y y);
    <Y extends Comparable<? super Y>> Predicate lessThanOrEqualTo(Expression<? extends Y> x, Expression<? extends Y> y);
    <Y extends Comparable<? super Y>> Predicate lessThanOrEqualTo(Expression<? extends Y> x, Y y);
    <Y extends Comparable<? super Y>> Predicate between(Expression<? extends Y> v, Expression<? extends Y> x, Expression<? extends Y> y);
    <Y extends Comparable<? super Y>> Predicate between(Expression<? extends Y> v, Y x, Y y);
    Predicate like(Expression<String> x, Expression<String> pattern);
    Predicate like(Expression<String> x, String pattern);
    Predicate notLike(Expression<String> x, Expression<String> pattern);
    Predicate notLike(Expression<String> x, String pattern);
    Expression<String> concat(Expression<String> x, Expression<String> y);
    Expression<String> upper(Expression<String> x);
    Expression<String> lower(Expression<String> x);
    Expression<String> trim(Expression<String> x);
    <N extends Number> Expression<N> sum(Expression<N> x);
    <N extends Number> Expression<N> sum(Expression<? extends N> x, Expression<? extends N> y);
    <N extends Number> Expression<N> diff(Expression<? extends N> x, Expression<? extends N> y);
    <N extends Number> Expression<N> max(Expression<N> x);
    <N extends Number> Expression<N> min(Expression<N> x);
    <X extends Comparable<? super X>> Expression<X> greatest(Expression<X> x);
    <X extends Comparable<? super X>> Expression<X> least(Expression<X> x);
    Expression<Long> count(Expression<?> x);
    Order asc(Expression<?> x);
    Order desc(Expression<?> x);
}''',
}


def write_jpa_stubs(root: Path) -> list[Path]:
    """javax.persistence (+ .criteria) as the generated entities and repositories use it, for compile tests."""
    paths = []
    for package, files in (('javax.persistence', JPA_API), ('javax.persistence.criteria', CRITERIA_API)):
        for name, body in files.items():
            path = root.joinpath(*package.split('.'), name + '.java')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('package ' + package + ';\n' + body + '\n', encoding='utf-8')
            paths.append(path)
    return paths
