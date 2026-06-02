package com.feihe.exception;

public class CdpCallFailedException extends RuntimeException {
    public CdpCallFailedException(String message) {
        super(message);
    }

    public CdpCallFailedException(String message, Throwable cause) {
        super(message, cause);
    }
}
