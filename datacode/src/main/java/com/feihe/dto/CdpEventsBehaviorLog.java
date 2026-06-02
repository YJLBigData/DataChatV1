package com.feihe.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.AllArgsConstructor;
import lombok.NoArgsConstructor;
import org.apache.commons.codec.digest.DigestUtils;

import java.util.Map;

/**
 * 事件上报请求
 */
public interface CdpEventsBehaviorLog {
    @lombok.Data
    class Param {
        private final String appkey;
        private final Integer ext;
        private String sign;
        private final String data;

        public Param(String appkey, String data, Integer ext) {
            this.appkey = appkey;
            this.data = data;
            this.ext = ext;
            this.sign = DigestUtils.md5Hex("data=" + data.length() + "&ext=" + ext).toUpperCase();

        }

        @lombok.Data
        @AllArgsConstructor
        @NoArgsConstructor
        @JsonInclude(JsonInclude.Include.NON_NULL)
        public static class EventsData {
            private String eventId;
            // 事件时间 ms 13位时间戳
            private String time;
            // 事件code
            private String event;
            // 普通事件填充为track
            private String type = "track";
            // 会话id (可选)
            private String sessionId;
            // 其他事件属性, key:属性key, value:属性值
            private Map<String, Object> properties;
            // 社交账号
            private Map<String, Object> account;
        }
    }

    @lombok.Data
    class Response {
        private Integer code;
        private String errMsg;
        private Object data;
    }
}
