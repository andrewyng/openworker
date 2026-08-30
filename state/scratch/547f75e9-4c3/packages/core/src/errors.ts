/**
 * Base error hierarchy for the broker.
 * Every error carries a stable `code` so the audit layer and agents can act
 * on failures without parsing messages.
 */
export class BrokerError extends Error {
  public readonly code: string;
  public readonly retryable: boolean;

  constructor(code: string, message: string, options?: { cause?: unknown; retryable?: boolean }) {
    super(message, { cause: options?.cause });
    this.name = new.target.name;
    this.code = code;
    this.retryable = options?.retryable ?? false;
  }
}

/** The request was denied by broker policy or the budget engine before the origin was reached. */
export class BudgetError extends BrokerError {
  constructor(message: string, code: string = 'BUDGET_DENIED', cause?: unknown) {
    super(code, message, { cause });
  }
}

/** A payment rail failed a quote, authorization, or settlement step. */
export class RailError extends BrokerError {
  constructor(message: string, code: string = 'RAIL_ERROR', cause?: unknown, retryable?: boolean) {
    super(code, message, { cause, retryable });
  }
}

/** Identity/attestation failure: missing, expired, or mismatched key. */
export class IdentityError extends BrokerError {
  constructor(message: string, code: string = 'IDENTITY_ERROR', cause?: unknown) {
    super(code, message, { cause });
  }
}

/** License parsing or enforcement failure. */
export class LicenseError extends BrokerError {
  constructor(message: string, code: string = 'LICENSE_ERROR', cause?: unknown) {
    super(code, message, { cause });
  }
}

/** The origin rejected the request outright (403 bot gate, WAF, agent detection). */
export class OriginRejectedError extends BrokerError {
  constructor(message: string, origin: string, httpStatus: number, cause?: unknown) {
    super(
      `ORIGIN_REJECTED`,
      `Origin ${origin} rejected the request with HTTP ${httpStatus}: ${message}`,
      { cause, retryable: true }
    );
  }
}

/** The origin demanded payment (402) and no rail could or should settle it. */
export class PaymentRequiredError extends BrokerError {
  constructor(message: string, code: string = 'PAYMENT_REQUIRED', cause?: unknown) {
    super(code, message, { cause, retryable: true });
  }
}

export function isBrokerError(err: unknown): err is BrokerError {
  return err instanceof BrokerError;
}
