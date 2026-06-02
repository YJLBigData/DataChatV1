package com.feihe.enumerate;

import lombok.AllArgsConstructor;
import lombok.Getter;

@AllArgsConstructor
@Getter
public enum Command {
    CUSTOMER("customer","/api/gateway/v1/cdp-entity/user/batchCreate"),
    ENTITY("entity","/api/gateway/v1/cdp-entity/open/dataBatchAdd"),
    EVENT_REALTIME("event-realtime","/events/api/trace"),
    EVENT_HISTORY("event-history","/events/history/trace"),
    EVENT_BACKTRACK("event-backtrack","/events/backtrack/trace"),
    CUSTOMER_UPDATE("customer-update","/api/gateway/v1/cdp-entity/user/batchEdit")
    ;
    private final String cmd;
    private final String api;
}
