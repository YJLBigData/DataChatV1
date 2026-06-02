package com.feihe.util;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;

public class DateUtil {
    private DateUtil() {
    }

    public static final String PARTITION_DATE_FORMAT = "yyyyMMdd";
    public static final String PARTITION_DATE_HOUR_FORMAT = "yyyyMMddHH";
    public static final String DATETIME_FORMAT = "yyyy-MM-dd HH:mm:ss";
    public static final String DATE_FORMAT = "yyyy-MM-dd";
    public static final String TIME_FORMAT = "HH:mm:ss";
    public static final DateTimeFormatter PARTITION_DATE_FORMATTER = DateTimeFormatter.ofPattern(
            PARTITION_DATE_FORMAT);
    public static final DateTimeFormatter PARTITION_DATE_HOUR_FORMATTER = DateTimeFormatter.ofPattern(
            PARTITION_DATE_HOUR_FORMAT);
    public static final DateTimeFormatter DATETIME_FORMATTER = DateTimeFormatter.ofPattern(
            DATETIME_FORMAT);
    public static final DateTimeFormatter DATE_FORMATTER = DateTimeFormatter.ofPattern(DATE_FORMAT);
    public static final DateTimeFormatter TIME_FORMATTER = DateTimeFormatter.ofPattern(TIME_FORMAT);

    public static String currentDay() {
        return LocalDate.now().format(PARTITION_DATE_FORMATTER);
    }

    public static String yesterday() {
        return LocalDate.now().minusDays(1).format(PARTITION_DATE_FORMATTER);
    }

    public static String currentDateTimeDsPartition() {
        return LocalDateTime.now().format(PARTITION_DATE_HOUR_FORMATTER);
    }

    public static LocalDateTime parseDateTime(String dateTime) {
        return LocalDateTime.parse(dateTime, DATETIME_FORMATTER);
    }

    public static LocalDate paresDate(String date) {
        return LocalDate.parse(date, DATE_FORMATTER);
    }

    public static Long currentTimeSeconds() {
        return ZonedDateTime.now().toInstant().getEpochSecond();
    }

    public static String currentTimeSecondsStr() {
        return String.valueOf(ZonedDateTime.now().toInstant().getEpochSecond());
    }

    public static String currentTimeMillis() {
        return String.valueOf(ZonedDateTime.now().toInstant().toEpochMilli());
    }

    public static String toEpochMilli(LocalDateTime dateTime) {
        return String.valueOf(dateTime
                .toInstant(
                        ZoneId
                                .systemDefault()
                                .getRules()
                                .getOffset(Instant.now())
                )
                .toEpochMilli());
    }
}
