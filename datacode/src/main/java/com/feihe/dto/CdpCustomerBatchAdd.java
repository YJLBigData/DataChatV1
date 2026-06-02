package com.feihe.dto;

import lombok.Data;
import lombok.experimental.Accessors;

import java.util.List;
import java.util.Map;

/**
 * 批量添加用户信息请求
 */

public interface CdpCustomerBatchAdd {
    @lombok.Data
    @Accessors
    class Param {
        // 1 <= contents.size() <= 100
        private List<Content> contents;

        @Data
        public static class Content {
            // 客户身份
            private List<IdentityValue> identity;
            // 客户属性
            private Map<String, Object> property;

            @Data
            @SuppressWarnings("java:S1700")
            public static class IdentityValue {
                // 客户身份类型
                private Integer identityType;
                // 客户身份值; identityType: 2 identityValue: 手机号
                private String identityValue;
            }
        }
    }

    @lombok.Data
    class Response {
        private Integer code;
        private String msg;
        private Object extra;
        private Response.Data data;
        private String requestId;

        @lombok.Data
        public static class Data {
            private Integer failCount;
            private Integer successCount;
            private List<Result> results;

            @lombok.Data
            public static class Result {
                private String cause;
                private Integer index;
                private String property;
                private String id;
            }
        }
    }
}
