package com.feihe.exception;

public class RdoReflectException extends RuntimeException {
    public RdoReflectException(String message) {
        super(message);
    }

    public RdoReflectException(Throwable e) {
        super(e);
    }

    public RdoReflectException(String message, Throwable cause) {
        super(message, cause);
    }
}
