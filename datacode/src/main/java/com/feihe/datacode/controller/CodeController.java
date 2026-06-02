package com.feihe.datacode.controller;

import com.feihe.datacode.config.DataCodeProperties;
import com.feihe.datacode.dto.Requests.GenerateCodeRequest;
import com.feihe.datacode.dto.Requests.ValidateSqlRequest;
import com.feihe.datacode.model.UserInfo;
import com.feihe.datacode.service.AuthService;
import com.feihe.datacode.service.CodeGenerationService;
import com.feihe.datacode.service.ExcelStandardizationService;
import com.feihe.datacode.service.RequirementParserService;
import com.feihe.datacode.service.SqlValidatorService;
import org.springframework.core.io.PathResource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/api/code")
public class CodeController {
    private final AuthService authService;
    private final RequirementParserService requirementParserService;
    private final SqlValidatorService sqlValidatorService;
    private final CodeGenerationService codeGenerationService;
    private final ExcelStandardizationService excelStandardizationService;
    private final DataCodeProperties properties;

    public CodeController(
            AuthService authService,
            RequirementParserService requirementParserService,
            SqlValidatorService sqlValidatorService,
            CodeGenerationService codeGenerationService,
            ExcelStandardizationService excelStandardizationService,
            DataCodeProperties properties) {
        this.authService = authService;
        this.requirementParserService = requirementParserService;
        this.sqlValidatorService = sqlValidatorService;
        this.codeGenerationService = codeGenerationService;
        this.excelStandardizationService = excelStandardizationService;
        this.properties = properties;
    }

    @PostMapping("/upload-requirements")
    public Map<String, Object> uploadRequirements(@RequestParam("files") List<MultipartFile> files,
                                                  @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        authService.requireUser(token);
        if (files == null || files.isEmpty()) {
            throw new IllegalArgumentException("请上传 Excel 或 CSV 文件");
        }
        Path uploadDir = Paths.get(properties.getUploadDir()).toAbsolutePath().normalize();
        Files.createDirectories(uploadDir);
        List<Map<String, Object>> parsed = new ArrayList<>();
        for (MultipartFile file : files) {
            if (file == null || file.isEmpty()) {
                continue;
            }
            String originalName = file.getOriginalFilename() == null ? "requirement.xlsx" : file.getOriginalFilename();
            byte[] data = file.getBytes();
            String savedName = Instant.now().getEpochSecond() + "_" + UUID.randomUUID().toString().substring(0, 8) + "_" + sanitizeFilename(originalName);
            Files.write(uploadDir.resolve(savedName), data);
            parsed.addAll(requirementParserService.parse(originalName, data));
        }
        if (parsed.isEmpty()) {
            throw new IllegalArgumentException("没有解析到有效需求内容");
        }
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("requirements", parsed);
        return payload;
    }

    @PostMapping("/validate")
    public Map<String, Object> validate(@RequestBody ValidateSqlRequest request,
                                        @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) {
        authService.requireUser(token);
        return sqlValidatorService.validateDataphinSql(request.sql).toPayload();
    }

    @PostMapping("/generate")
    public Map<String, Object> generate(@RequestBody GenerateCodeRequest request,
                                        @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        UserInfo user = authService.requireUser(token);
        return codeGenerationService.generate(request, user);
    }

    @PostMapping("/standardize-excel")
    public Map<String, Object> standardizeExcel(@RequestParam("file") MultipartFile file,
                                                @RequestParam(value = "prompt", required = false) String prompt,
                                                @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        authService.requireUser(token);
        if (file == null || file.isEmpty()) {
            throw new IllegalArgumentException("请上传需要标准化的 Excel 文件");
        }
        String originalName = file.getOriginalFilename() == null ? "source.xlsx" : file.getOriginalFilename();
        return excelStandardizationService.standardize(originalName, file.getBytes(), prompt);
    }

    @GetMapping("/download-normalized/{fileId}")
    public ResponseEntity<PathResource> downloadNormalized(@org.springframework.web.bind.annotation.PathVariable String fileId,
                                                           @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) {
        authService.requireUser(token);
        Path path = excelStandardizationService.resolveDownloadPath(fileId);
        if (!Files.exists(path)) {
            throw new IllegalArgumentException("标准化 Excel 文件不存在或已被清理");
        }
        PathResource resource = new PathResource(path);
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, ContentDisposition.attachment()
                        .filename("normalized_" + fileId + ".xlsx")
                        .build()
                        .toString())
                .contentType(MediaType.parseMediaType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
                .body(resource);
    }

    private String sanitizeFilename(String name) {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < name.length() && builder.length() < 120; i++) {
            char ch = name.charAt(i);
            if (Character.isLetterOrDigit(ch) || ch == '.' || ch == '_' || ch == '-') {
                builder.append(ch);
            } else {
                builder.append('_');
            }
        }
        return builder.length() == 0 ? "upload" : builder.toString();
    }
}
