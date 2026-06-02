package com.feihe.util;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.json.JsonReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.module.SimpleModule;
import com.fasterxml.jackson.datatype.jsr310.PackageVersion;
import com.fasterxml.jackson.datatype.jsr310.deser.LocalDateDeserializer;
import com.fasterxml.jackson.datatype.jsr310.deser.LocalDateTimeDeserializer;
import com.fasterxml.jackson.datatype.jsr310.deser.LocalTimeDeserializer;
import com.fasterxml.jackson.datatype.jsr310.ser.LocalDateSerializer;
import com.fasterxml.jackson.datatype.jsr310.ser.LocalDateTimeSerializer;
import com.fasterxml.jackson.datatype.jsr310.ser.LocalTimeSerializer;
import lombok.extern.slf4j.Slf4j;

import java.io.Serializable;
import java.text.SimpleDateFormat;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;

@Slf4j
public class JsonUtil {
    private JsonUtil() {
    }

    public static <T> String toJson(T value) {
        try {
            return getInstance().writeValueAsString(value);
        } catch (Exception e) {
            log.error("convert to json error: {}", e.getMessage(), e);
        }
        return null;
    }

    public static <T> T fromJson(String json, Class<T> clazz) {
        try {
            return getInstance().readValue(json, clazz);
        } catch (Exception e) {
            log.error("convert to bean error: {}", e.getMessage(), e);
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    public static <T> List<Map<String, String>> convertToMapList(
            List<T> entityList,
            boolean beanMap) {
        List<Map<String, String>> result = new ArrayList<>();

        for (T entity : entityList) {
            Map<String, Object> tempMap = getInstance().convertValue(entity, Map.class);
            Map<String, String> stringMap = new HashMap<>();
            for (Map.Entry<String, Object> entry : tempMap.entrySet()) {

                String key = entry.getKey();
                if (!beanMap) {
                    key = OdpsUtil.camelToSnake(entry.getKey());
                }
                stringMap.put(
                        key,
                        null != entry.getValue() ? entry.getValue().toString() : null);
            }
            result.add(stringMap);
        }
        return result;
    }

    private static ObjectMapper getInstance() {
        return JacksonHolder.INSTANCE;
    }

    private static class JacksonHolder {
        private static final ObjectMapper INSTANCE = new JacksonObjectMapper();
    }

    private static class JacksonObjectMapper extends ObjectMapper implements Serializable {
        private static final long serialVersionUID = 4288193147502386170L;

        private static final Locale CHINA = Locale.CHINA;

        public JacksonObjectMapper(ObjectMapper src) {
            super(src);
        }

        public JacksonObjectMapper() {
            super();
            super.setLocale(CHINA);
            super.configure(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS, false);
            super.setTimeZone(TimeZone.getTimeZone(ZoneId.systemDefault()));
            super.setDateFormat(new SimpleDateFormat(DateUtil.DATETIME_FORMAT, Locale.CHINA));
            // 单引号
            super.configure(JsonParser.Feature.ALLOW_SINGLE_QUOTES, true);
            // 允许 JSON 字符串包含非引号控制字符（值小于 32 的 ASCII 字符，包含制表符和换行符）
            super.configure(JsonReadFeature.ALLOW_UNESCAPED_CONTROL_CHARS.mappedFeature(), true);
            super.configure(
                    JsonReadFeature.ALLOW_BACKSLASH_ESCAPING_ANY_CHARACTER.mappedFeature(),
                    true);
            //失败处理
            super.configure(SerializationFeature.FAIL_ON_EMPTY_BEANS, false);
            super.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);
            //单引号处理
            super.configure(JsonReadFeature.ALLOW_SINGLE_QUOTES.mappedFeature(), true);
            //反序列化时，属性不存在的兼容处理
            super.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);
            //日期格式化 - 注册自定义模块覆盖默认的 JavaTime 模块
            super.registerModule(CdpJavaTimeModule.INSTANCE);
        }

        @Override
        public ObjectMapper copy() {
            return new JacksonObjectMapper(this);
        }
    }

    private static class CdpJavaTimeModule extends SimpleModule {
        public static final CdpJavaTimeModule INSTANCE = new CdpJavaTimeModule();

        public CdpJavaTimeModule() {
            super(PackageVersion.VERSION);
            this.addDeserializer(
                    LocalDateTime.class,
                    new LocalDateTimeDeserializer(DateUtil.DATETIME_FORMATTER));
            this.addDeserializer(
                    LocalDate.class,
                    new LocalDateDeserializer(DateUtil.DATE_FORMATTER));
            this.addDeserializer(
                    LocalTime.class,
                    new LocalTimeDeserializer(DateUtil.TIME_FORMATTER));
            this.addSerializer(
                    LocalDateTime.class,
                    new LocalDateTimeSerializer(DateUtil.DATETIME_FORMATTER));
            this.addSerializer(
                    LocalDate.class,
                    new LocalDateSerializer(DateUtil.DATE_FORMATTER));
            this.addSerializer(
                    LocalTime.class,
                    new LocalTimeSerializer(DateUtil.TIME_FORMATTER));
        }
    }
}
