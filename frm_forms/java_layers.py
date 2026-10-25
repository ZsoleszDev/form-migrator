"""CL contracts and HTTP adapter, DPS controllers, WBS delegation."""
from pathlib import Path
from .common import jstr, name
from .generate import write


def generate_layers(model, output: Path, config, module, package):
    cl, dps, wbs = (package + suffix for suffix in (".cl", ".dps", ".wbs"))
    folders = {layer: output / "backend" / layer for layer in ("CL", "DPS", "WBS")}
    for layer, folder in folders.items():
        folder.mkdir(parents=True, exist_ok=True)
        write(folder / "package-info.java", f"/** Generated {layer} feature; integrate in the corresponding host project. */\npackage {package}.{layer.lower()};\n")
    write(folders["CL"] / "ConstantsBase.java", f'''package {cl};

/** Module-local separator. Replace with your existing ConstantsBase if desired. */
public final class ConstantsBase {{
    private ConstantsBase() {{}}
    public static final String PD = "/";
}}
''')
    client_beans = []
    module_class = name(module, "pascal")
    for block in model["blocks"]:
        if not block["database"]:
            continue
        cls = block["class"]
        imports = f"import java.util.List;\nimport {cl}.*;\nimport {cl}.{cls}Dtos.*;\n"
        base_path = config["api_prefix"].rstrip("/") + "/" + module + "/" + block["key"]
        constants = [f"    public static final String BASE_PATH = {jstr(base_path)};"]
        for key, constant in (("list", "GET_TABLE_DATA"), ("create", "CREATE"), ("update", "UPDATE"), ("delete", "DELETE")):
            constants.extend([f"    public static final String {constant}_NAME = {jstr(config['endpoint_names'][key])};",
                              f"    public static final String {constant}_PATH = ConstantsBase.PD + {constant}_NAME;"])
        write(folders["CL"] / f"{cls}Constants.java", f"package {cl};\n\npublic final class {cls}Constants {{\n    private {cls}Constants() {{}}\n" + "\n".join(constants) + "\n}\n")
        write(folders["CL"] / f"{cls}Dtos.java", f'''package {cl};
import java.util.List;

public final class {cls}Dtos {{
    private {cls}Dtos() {{}}
    public record RowResult({cls}Row row, List<String> messages) {{}}
    public record PageResult(List<{cls}Row> rows, List<String> messages) {{}}
    public record UpdateRequest({cls}Row original, {cls}Row value) {{}}
}}
''')
        methods = f'''    PageResult list(int offset, int limit);
    RowResult create({cls}Row row);
    RowResult update({cls}Row original, {cls}Row value);
    List<String> delete({cls}Row original);
'''
        write(folders["CL"] / f"{cls}RestClient.java", f"package {cl};\n{imports}\npublic interface {cls}RestClient {{\n{methods}}}\n")
        write(folders["CL"] / f"{cls}RestClientImpl.java", f'''package {cl};
{imports}
import java.util.Objects;
import java.util.function.Supplier;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;

/** Register in WBS. The host configures authentication and timeouts on the builder. */
public class {cls}RestClientImpl implements {cls}RestClient {{
    private final RestClient client;
    public {cls}RestClientImpl(RestClient client) {{ this.client = Objects.requireNonNull(client); }}
    @Override public PageResult list(int offset, int limit) {{
        return call(() -> client.get().uri({cls}Constants.BASE_PATH + {cls}Constants.GET_TABLE_DATA_PATH + "?offset={{offset}}&limit={{limit}}", offset, limit)
            .retrieve().body(PageResult.class));
    }}
    @Override public RowResult create({cls}Row row) {{
        return call(() -> client.post().uri({cls}Constants.BASE_PATH + {cls}Constants.CREATE_PATH)
            .body(row).retrieve().body(RowResult.class));
    }}
    @Override public RowResult update({cls}Row original, {cls}Row value) {{
        return call(() -> client.put().uri({cls}Constants.BASE_PATH + {cls}Constants.UPDATE_PATH)
            .body(new UpdateRequest(original, value)).retrieve().body(RowResult.class));
    }}
    @Override public List<String> delete({cls}Row original) {{
        return call(() -> client.method(HttpMethod.DELETE).uri({cls}Constants.BASE_PATH + {cls}Constants.DELETE_PATH)
            .body(original).retrieve().body(new ParameterizedTypeReference<List<String>>() {{}}));
    }}
    private <T> T call(Supplier<T> request) {{
        try {{
            T result = request.get();
            if (result == null) throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "Üres DPS-válasz.");
            return result;
        }} catch (RestClientResponseException error) {{
            // Preserve 401/403/409/422/501 without disclosing raw DPS error bodies.
            throw new ResponseStatusException(error.getStatusCode(), "A DPS elutasította a kérést.");
        }} catch (RestClientException error) {{
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "A DPS nem érhető el.");
        }}
    }}
}}
''')
        for layer, layer_package in (("DPS", dps), ("WBS", wbs)):
            write(folders[layer] / f"{cls}Service.java", f"package {layer_package};\n{imports}\npublic interface {cls}Service {{\n{methods}}}\n")
            write(folders[layer] / f"{cls}Controller.java", f'''package {layer_package};
{imports}
import org.springframework.web.bind.annotation.*;
import org.springframework.http.HttpStatus;

public interface {cls}Controller {{
    @GetMapping({cls}Constants.GET_TABLE_DATA_PATH)
    PageResult list(@RequestParam(name="offset", defaultValue="0") int offset,
                    @RequestParam(name="limit", defaultValue="50") int limit);
    @PostMapping({cls}Constants.CREATE_PATH)
    @ResponseStatus(HttpStatus.CREATED)
    RowResult create(@RequestBody {cls}Row row);
    @PutMapping({cls}Constants.UPDATE_PATH)
    RowResult update(@RequestBody UpdateRequest request);
    @DeleteMapping({cls}Constants.DELETE_PATH)
    List<String> delete(@RequestBody {cls}Row original);
}}
''')
            write(folders[layer] / f"{cls}ControllerBase.java", f'''package {layer_package};
{imports}
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

public abstract class {cls}ControllerBase implements {cls}Controller {{
    protected final {cls}Service service;
    protected {cls}ControllerBase({cls}Service service) {{ this.service = service; }}
    @Override public PageResult list(int offset, int limit) {{ return service.list(offset, limit); }}
    @Override public RowResult create({cls}Row row) {{ return service.create(row); }}
    @Override public RowResult update(UpdateRequest request) {{
        if (request == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
        return service.update(request.original(), request.value());
    }}
    @Override public List<String> delete({cls}Row original) {{ return service.delete(original); }}
}}
''')
            write(folders[layer] / f"{cls}ControllerImpl.java", f'''package {layer_package};
import {cl}.{cls}Constants;
import org.springframework.web.bind.annotation.*;

@RestController({jstr(layer_package + '.' + cls + 'Controller')})
@RequestMapping({cls}Constants.BASE_PATH)
public class {cls}ControllerImpl extends {cls}ControllerBase {{
    public {cls}ControllerImpl({cls}Service service) {{ super(service); }}
}}
''')
        write(folders["WBS"] / f"{cls}ServiceBase.java", f'''package {wbs};
{imports}

public abstract class {cls}ServiceBase implements {cls}Service {{
    protected final {cls}RestClient client;
    private final MigrationAccess access;
    protected {cls}ServiceBase({cls}RestClient client, MigrationAccess access) {{ this.client = client; this.access = access; }}
    @Override public PageResult list(int offset, int limit) {{ access.check({jstr(block['name'])}, "read"); return client.list(offset, limit); }}
    @Override public RowResult create({cls}Row row) {{ access.check({jstr(block['name'])}, "create"); return client.create(row); }}
    @Override public RowResult update({cls}Row original, {cls}Row value) {{ access.check({jstr(block['name'])}, "update"); return client.update(original, value); }}
    @Override public List<String> delete({cls}Row original) {{ access.check({jstr(block['name'])}, "delete"); return client.delete(original); }}
}}
''')
        write(folders["WBS"] / f"{cls}ServiceImpl.java", f'''package {wbs};
import {cl}.*;
import org.springframework.stereotype.Service;

@Service({jstr(wbs + '.' + cls + 'Service')})
public class {cls}ServiceImpl extends {cls}ServiceBase {{
    public {cls}ServiceImpl({cls}RestClient client, MigrationAccess access) {{ super(client, access); }}
}}
''')
        client_beans.append(f'''    @Bean(name={jstr(wbs + '.' + cls + 'RestClient')})
    public {cls}RestClient {name(cls)}RestClient(@Qualifier(HTTP_CLIENT_BEAN) RestClient client) {{
        return new {cls}RestClientImpl(client);
    }}
''')
    if client_beans:
        write(folders["WBS"] / f"{module_class}DpsClientConfiguration.java", f'''package {wbs};
import {cl}.*;
import java.net.URI;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

@Configuration({jstr(wbs + '.DpsClientConfiguration')})
public class {module_class}DpsClientConfiguration {{
    public static final String HTTP_CLIENT_BEAN = {jstr(wbs + '.dpsHttpClient')};
    private static final String GENERATED_DPS_URL = {jstr(config['dps_base_url'])};
    @Bean(name=HTTP_CLIENT_BEAN)
    public RestClient dpsHttpClient(RestClient.Builder builder,
        @Value("${{frm.{module}.dps-base-url:}}") String configuredUrl) {{
        String url = configuredUrl == null || configuredUrl.isBlank() ? GENERATED_DPS_URL : configuredUrl.trim();
        URI uri = URI.create(url);
        if (!("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())) || uri.getHost() == null
            || uri.getRawUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null)
            throw new IllegalArgumentException("Állítsd be: frm.{module}.dps-base-url");
        return builder.clone().baseUrl(url.replaceAll("/+$", "")).build();
    }}
{''.join(client_beans)}
}}
''')
