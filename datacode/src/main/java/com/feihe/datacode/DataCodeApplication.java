package com.feihe.datacode;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = "com.feihe")
public class DataCodeApplication {
    public static void main(String[] args) {
        SpringApplication.run(DataCodeApplication.class, args);
    }
}
