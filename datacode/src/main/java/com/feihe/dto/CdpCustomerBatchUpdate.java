package com.feihe.dto;

import java.util.List;
import java.util.Map;

public interface CdpCustomerBatchUpdate {
    @lombok.Data
    class Response {
        private Integer code;
        private String msg;
        private Object extra;
        private CdpCustomerBatchUpdate.Response.Data data;
        private String requestId;

        @lombok.Data
        public static class Data {
            private List<String> succRecords;
            private List<FailRecord> failRecords;

            @lombok.Data
            public static class FailRecord {
                private Map<String, Object> rawData;
                private String cause;
                private Integer index;
            }
        }
    }
}
