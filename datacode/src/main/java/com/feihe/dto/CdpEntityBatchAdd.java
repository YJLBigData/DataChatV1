package com.feihe.dto;

import lombok.experimental.Accessors;

import java.util.List;
import java.util.Map;

/**
 * 批量添加自定义实体数据请求
 */

public interface CdpEntityBatchAdd {
    @lombok.Data
    @Accessors
    class Param {
        // 实体Key rdo class name
        private String entityKey;
        // 1<= contents.size() <=500
        private List<Content> contents;

        @lombok.Data
        public static class Content {
            // 实体属性 key: rdo attribute name, value: rdo attribute value
            private Map<String, Object> property;
            private List<IdentityValue> identity;

            @lombok.Data
            @SuppressWarnings("java:S1700")
            public static class IdentityValue {
                private String identityKey;
                private String identityValue;
            }
        }
    }

    @lombok.Data
    class Response {
        private Integer code;
        private String msg;
        private Object extra;
        private Data data;
        private String requestId;

        @lombok.Data
        public static class Data {
            private Integer failCount;
            private Integer successCount;
            private List<RetRecord> failRecordList;
            private List<RetRecord> successList;

            @lombok.Data
            public static class RetRecord {
                private Map<String, Object> rawData;
                private String cause;
                private String keyId;
                private String id;
                private Boolean success;
            }
        }
    }
}
