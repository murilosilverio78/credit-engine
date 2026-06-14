"use client";

import { useCallback, useEffect, useRef } from "react";

const IDLE_TIMEOUT_MS = 30 * 60 * 1000;
const WARNING_BEFORE_MS = 2 * 60 * 1000;
const WARNING_AT_MS = IDLE_TIMEOUT_MS - WARNING_BEFORE_MS;
const ACTIVITY_EVENTS = ["mousemove", "mousedown", "keydown", "touchstart", "scroll"] as const;

type IdleState = "active" | "warning" | "expired";

type IdleCallbacks = {
  onWarning: () => void;
  onExpire: () => void;
};

export function useIdleTimeout(callbacks: IdleCallbacks): { resetTimer: () => void } {
  const callbacksRef = useRef(callbacks);
  const stateRef = useRef<IdleState>("active");
  const warningTimerRef = useRef<number | null>(null);
  const expireTimerRef = useRef<number | null>(null);

  useEffect(() => {
    callbacksRef.current = callbacks;
  }, [callbacks]);

  const clearTimers = useCallback(() => {
    if (warningTimerRef.current) {
      window.clearTimeout(warningTimerRef.current);
      warningTimerRef.current = null;
    }
    if (expireTimerRef.current) {
      window.clearTimeout(expireTimerRef.current);
      expireTimerRef.current = null;
    }
  }, []);

  const resetTimer = useCallback(() => {
    clearTimers();
    stateRef.current = "active";

    warningTimerRef.current = window.setTimeout(() => {
      stateRef.current = "warning";
      callbacksRef.current.onWarning();

      expireTimerRef.current = window.setTimeout(() => {
        stateRef.current = "expired";
        callbacksRef.current.onExpire();
      }, WARNING_BEFORE_MS);
    }, WARNING_AT_MS);
  }, [clearTimers]);

  useEffect(() => {
    const handleActivity = () => {
      if (stateRef.current === "expired") {
        return;
      }
      resetTimer();
    };

    resetTimer();
    ACTIVITY_EVENTS.forEach((eventName) => {
      window.addEventListener(eventName, handleActivity, { passive: true });
    });

    return () => {
      clearTimers();
      ACTIVITY_EVENTS.forEach((eventName) => {
        window.removeEventListener(eventName, handleActivity);
      });
    };
  }, [clearTimers, resetTimer]);

  return { resetTimer };
}
