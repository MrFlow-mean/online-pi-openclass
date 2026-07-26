"use client";

import { CreditCard, ExternalLink, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { CreditPackage, PayPalClientConfig, PayPalPaymentMethod } from "@/lib/api";

type PayPalOrderData = { orderID: string };

type PayPalButtonsInstance = {
  isEligible(): boolean;
  render(target: string | HTMLElement): Promise<void>;
  close?(): Promise<void>;
};

type PayPalCardField = {
  render(target: string): Promise<void>;
};

type PayPalCardFieldsInstance = {
  isEligible(): boolean;
  NameField(options?: Record<string, unknown>): PayPalCardField;
  NumberField(options?: Record<string, unknown>): PayPalCardField;
  ExpiryField(options?: Record<string, unknown>): PayPalCardField;
  CVVField(options?: Record<string, unknown>): PayPalCardField;
  submit(options?: Record<string, unknown>): Promise<void>;
  close?(): Promise<void>;
};

type PayPalWalletClient = {
  config(): Promise<Record<string, unknown>>;
  confirmOrder(options: Record<string, unknown>): Promise<{ status: string }>;
  initiatePayerAction?(options: { orderId: string }): Promise<unknown>;
  validateMerchant?(options: { validationUrl: string; displayName: string }): Promise<{
    merchantSession: unknown;
  }>;
};

type PayPalNamespace = {
  Buttons(options: Record<string, unknown>): PayPalButtonsInstance;
  CardFields?(options: Record<string, unknown>): PayPalCardFieldsInstance;
  Applepay?(): PayPalWalletClient;
  Googlepay?(): PayPalWalletClient;
};

type ApplePayPaymentRequest = {
  countryCode: string;
  merchantCapabilities: string[];
  supportedNetworks: string[];
  currencyCode: string;
  total: { label: string; type: "final"; amount: string };
};

type ApplePaySessionInstance = {
  onvalidatemerchant: ((event: { validationURL: string }) => void) | null;
  onpaymentauthorized: ((event: { payment: { token: unknown; billingContact?: unknown } }) => void) | null;
  oncancel: (() => void) | null;
  completeMerchantValidation(session: unknown): void;
  completePayment(status: number): void;
  abort(): void;
  begin(): void;
};

type ApplePaySessionConstructor = {
  new (version: number, request: ApplePayPaymentRequest): ApplePaySessionInstance;
  STATUS_SUCCESS: number;
  STATUS_FAILURE: number;
  canMakePayments(): boolean;
};

type GooglePaymentsClient = {
  isReadyToPay(request: Record<string, unknown>): Promise<{ result: boolean }>;
  createButton(options: Record<string, unknown>): HTMLElement;
  loadPaymentData(request: Record<string, unknown>): Promise<unknown>;
};

type GoogleNamespace = {
  payments: {
    api: {
      PaymentsClient: new (options: Record<string, unknown>) => GooglePaymentsClient;
    };
  };
};

type PaymentBrowserWindow = Window & {
  paypal?: PayPalNamespace;
  ApplePaySession?: ApplePaySessionConstructor;
  google?: GoogleNamespace;
};

type PayPalPaymentMethodsProps = {
  paymentPackage: CreditPackage;
  disabled: boolean;
  onBusyChange: (busy: boolean) => void;
  onSuccess: () => Promise<void>;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
};

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function loadScript(id: string, source: string, attributes: Record<string, string> = {}) {
  return new Promise<void>((resolve, reject) => {
    const existing = document.getElementById(id) as HTMLScriptElement | null;
    if (existing?.dataset.loaded === "true") {
      resolve();
      return;
    }
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error(`无法加载 ${id}`)), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.id = id;
    script.src = source;
    script.async = true;
    Object.entries(attributes).forEach(([name, value]) => script.setAttribute(name, value));
    script.addEventListener(
      "load",
      () => {
        script.dataset.loaded = "true";
        resolve();
      },
      { once: true }
    );
    script.addEventListener("error", () => reject(new Error(`无法加载 ${id}`)), { once: true });
    document.head.appendChild(script);
  });
}

async function loadPayPalSdk(config: PayPalClientConfig) {
  const parameters = new URLSearchParams({
    "client-id": config.client_id,
    components: "buttons,card-fields,applepay,googlepay",
    currency: config.currency,
    intent: "capture",
  });
  await loadScript(
    "openclass-paypal-sdk",
    `https://www.paypal.com/sdk/js?${parameters.toString()}`,
    { "data-client-token": config.client_token }
  );
  const paymentWindow = window as PaymentBrowserWindow;
  if (!paymentWindow.paypal) {
    throw new Error("PayPal 支付组件未正确初始化");
  }
  return paymentWindow.paypal;
}

export function PayPalPaymentMethods({
  paymentPackage,
  disabled,
  onBusyChange,
  onSuccess,
  onError,
  onNotice,
}: PayPalPaymentMethodsProps) {
  const [sdkLoading, setSdkLoading] = useState(true);
  const [cardEligible, setCardEligible] = useState(false);
  const [applePayEligible, setApplePayEligible] = useState(false);
  const [googlePayEligible, setGooglePayEligible] = useState(false);
  const [cardSubmitting, setCardSubmitting] = useState(false);
  const [redirecting, setRedirecting] = useState(false);
  const cardFieldsRef = useRef<PayPalCardFieldsInstance | null>(null);

  useEffect(() => {
    let disposed = false;
    let buttons: PayPalButtonsInstance | null = null;
    let cardFields: PayPalCardFieldsInstance | null = null;

    const setBusy = (busy: boolean) => {
      if (!disposed) {
        onBusyChange(busy);
      }
    };

    const captureOrder = async (orderId: string) => {
      setBusy(true);
      try {
        await api.capturePayPalOrder(orderId);
        await onSuccess();
      } catch (error) {
        onError(errorMessage(error, "PayPal 付款确认失败"));
        throw error;
      } finally {
        setBusy(false);
      }
    };

    const createOrder = async (paymentMethod: PayPalPaymentMethod) => {
      const order = await api.createPayPalOrder(paymentPackage.id, paymentMethod);
      return order.order_id;
    };

    async function initialize() {
      setSdkLoading(true);
      setCardEligible(false);
      setApplePayEligible(false);
      setGooglePayEligible(false);
      cardFieldsRef.current = null;
      document.getElementById("paypal-button-container")?.replaceChildren();
      document.getElementById("apple-pay-button-container")?.replaceChildren();
      document.getElementById("google-pay-button-container")?.replaceChildren();

      try {
        const config = await api.getPayPalClientConfig();
        const paypal = await loadPayPalSdk(config);
        if (disposed) return;

        buttons = paypal.Buttons({
          fundingSource: "paypal",
          createOrder: () => createOrder("paypal"),
          onApprove: (data: PayPalOrderData) => captureOrder(data.orderID),
          onCancel: () => onNotice("你已取消本次付款，没有产生扣款。"),
          onError: (error: unknown) => onError(errorMessage(error, "PayPal 付款失败")),
          style: { layout: "vertical", shape: "rect", color: "black", label: "paypal", height: 48 },
        });
        if (buttons.isEligible()) {
          await buttons.render("#paypal-button-container");
        }

        if (paypal.CardFields) {
          cardFields = paypal.CardFields({
            createOrder: () => createOrder("card"),
            onApprove: (data: PayPalOrderData) => captureOrder(data.orderID),
            onError: (error: unknown) => onError(errorMessage(error, "银行卡付款失败")),
            style: {
              input: {
                "font-size": "16px",
                "font-family": "system-ui, sans-serif",
                color: "#1c1917",
                border: "none",
                outline: "none",
                padding: "14px 12px",
                "background-color": "#ffffff",
              },
              ".invalid": { color: "#b91c1c" },
            },
          });
          if (cardFields.isEligible()) {
            setCardEligible(true);
            cardFieldsRef.current = cardFields;
            await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
            await Promise.all([
              cardFields.NameField({ placeholder: "持卡人姓名" }).render("#paypal-card-name"),
              cardFields.NumberField({ placeholder: "卡号" }).render("#paypal-card-number"),
              cardFields.ExpiryField({ placeholder: "有效期" }).render("#paypal-card-expiry"),
              cardFields.CVVField({ placeholder: "安全码" }).render("#paypal-card-cvv"),
            ]);
          }
        }

        const paymentWindow = window as PaymentBrowserWindow;
        await Promise.allSettled([
          initializeApplePay(
            paypal,
            paymentWindow,
            config,
            paymentPackage,
            createOrder,
            captureOrder,
            () => disposed,
            setApplePayEligible,
            onError
          ),
          initializeGooglePay(
            paypal,
            paymentWindow,
            config,
            paymentPackage,
            createOrder,
            captureOrder,
            () => disposed,
            setGooglePayEligible,
            onError
          ),
        ]);
      } catch (error) {
        if (!disposed) {
          onError(errorMessage(error, "PayPal 支付组件暂时不可用，仍可前往 PayPal 安全页面付款。"));
        }
      } finally {
        if (!disposed) setSdkLoading(false);
      }
    }

    void initialize();
    return () => {
      disposed = true;
      cardFieldsRef.current = null;
      void buttons?.close?.();
      void cardFields?.close?.();
    };
  }, [onBusyChange, onError, onNotice, onSuccess, paymentPackage]);

  async function submitCard() {
    if (!cardFieldsRef.current) return;
    setCardSubmitting(true);
    onBusyChange(true);
    try {
      await cardFieldsRef.current.submit();
    } catch (error) {
      onError(errorMessage(error, "请检查银行卡信息后重试"));
    } finally {
      setCardSubmitting(false);
      onBusyChange(false);
    }
  }

  async function redirectToPayPal() {
    setRedirecting(true);
    onBusyChange(true);
    try {
      const order = await api.createPayPalOrder(paymentPackage.id, "redirect");
      if (!order.approve_url) throw new Error("PayPal 未返回付款地址");
      window.location.assign(order.approve_url);
    } catch (error) {
      onError(errorMessage(error, "无法创建 PayPal 订单"));
      setRedirecting(false);
      onBusyChange(false);
    }
  }

  return (
    <section className="mx-auto mt-8 max-w-md" data-testid="paypal-checkout-panel">
      <div className="flex items-start justify-between gap-5 border-b border-stone-200 pb-5">
        <div>
          <p className="text-sm font-semibold text-stone-950">付款</p>
          <p className="mt-1 text-sm text-stone-500">
            购买 {paymentPackage.credits.toLocaleString()} 点数
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-lg font-semibold text-stone-950">${paymentPackage.amount_usd}</span>
          {sdkLoading ? <LoaderCircle className="h-4 w-4 animate-spin text-stone-400" /> : null}
        </div>
      </div>

      <div className="mt-6">
        <p className="text-sm font-semibold text-stone-950">快捷付款</p>
        <div
          className={`mt-3 grid gap-3 ${applePayEligible || googlePayEligible ? "sm:grid-cols-2" : ""}`}
        >
          <div className="min-h-12 overflow-hidden rounded-lg" id="paypal-button-container" />
          <div id="apple-pay-button-container" className={applePayEligible ? "min-h-12" : "hidden"} />
          <div id="google-pay-button-container" className={googlePayEligible ? "min-h-12" : "hidden"} />
        </div>
      </div>

      <div className={cardEligible ? "my-6 flex items-center gap-3" : "hidden"} aria-hidden="true">
        <span className="h-px flex-1 bg-stone-200" />
        <span className="text-xs text-stone-400">或使用银行卡</span>
        <span className="h-px flex-1 bg-stone-200" />
      </div>

      <div className={cardEligible ? "block" : "hidden"} data-testid="paypal-card-fields">
        <div className="rounded-xl border border-stone-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-stone-200 px-4 py-4">
            <span className="flex items-center gap-2 text-sm font-semibold text-stone-900">
              <span className="flex h-5 w-5 items-center justify-center rounded-full border-[5px] border-stone-950" />
              <CreditCard className="h-4 w-4" />
              银行卡
            </span>
            <span className="text-xs font-medium tracking-wide text-stone-400">VISA · MASTERCARD</span>
          </div>

          <div className="p-4">
            <label className="mb-2 block text-xs font-medium text-stone-500">银行卡信息</label>
            <div className="overflow-hidden rounded-lg border border-stone-300 bg-white shadow-sm">
              <div id="paypal-card-number" className="h-[52px] overflow-hidden" />
              <div className="grid grid-cols-2 border-t border-stone-300">
                <div id="paypal-card-expiry" className="h-[52px] overflow-hidden" />
                <div id="paypal-card-cvv" className="h-[52px] overflow-hidden border-l border-stone-300" />
              </div>
            </div>

            <label className="mb-2 mt-4 block text-xs font-medium text-stone-500">持卡人姓名</label>
            <div
              id="paypal-card-name"
              className="h-[52px] overflow-hidden rounded-lg border border-stone-300 bg-white shadow-sm"
            />
          </div>
        </div>

        <button
          type="button"
          onClick={() => void submitCard()}
          disabled={disabled || cardSubmitting}
          className="mt-6 inline-flex h-14 w-full items-center justify-center gap-2 rounded-lg bg-[#0878d1] px-4 text-base font-semibold text-white shadow-sm transition hover:bg-[#066ab9] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {cardSubmitting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
          {cardSubmitting ? "正在确认付款" : `支付 $${paymentPackage.amount_usd}`}
        </button>
      </div>

      <button
        type="button"
        onClick={() => void redirectToPayPal()}
        disabled={disabled || redirecting}
        className="mx-auto mt-5 flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-stone-500 transition hover:bg-stone-100 hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {redirecting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}
        {redirecting ? "正在前往 PayPal" : "在 PayPal 安全页面付款"}
      </button>

      <p className="mt-5 text-center text-xs leading-5 text-stone-400">
        付款方式由 PayPal 根据账户、地区和设备资格提供。支付数据由 PayPal 按其
        <a
          href="https://www.paypal.com/c2/legalhub/paypal/privacy-full"
          target="_blank"
          rel="noreferrer"
          className="ml-1 underline underline-offset-2 hover:text-stone-600"
        >
          隐私声明
        </a>
        处理。
      </p>
    </section>
  );
}

async function initializeApplePay(
  paypal: PayPalNamespace,
  paymentWindow: PaymentBrowserWindow,
  clientConfig: PayPalClientConfig,
  paymentPackage: CreditPackage,
  createOrder: (method: PayPalPaymentMethod) => Promise<string>,
  captureOrder: (orderId: string) => Promise<void>,
  isDisposed: () => boolean,
  setEligible: (eligible: boolean) => void,
  reportError: (message: string) => void
) {
  if (!paypal.Applepay) return;
  await loadScript(
    "openclass-apple-pay-sdk",
    "https://applepay.cdn-apple.com/jsapi/1.latest/apple-pay-sdk.js"
  );
  const ApplePaySession = paymentWindow.ApplePaySession;
  if (isDisposed() || !ApplePaySession?.canMakePayments()) return;
  const applePay = paypal.Applepay();
  const config = await applePay.config();
  if (!config.isEligible) return;

  const container = document.getElementById("apple-pay-button-container");
  if (!container) return;
  const button = document.createElement("apple-pay-button");
  button.setAttribute("buttonstyle", "black");
  button.setAttribute("type", "pay");
  button.setAttribute("locale", "zh-CN");
  button.style.display = "block";
  button.style.width = "100%";
  button.style.height = "48px";
  button.addEventListener("click", () => {
    const session = new ApplePaySession(4, {
      countryCode: String(config.countryCode),
      merchantCapabilities: config.merchantCapabilities as string[],
      supportedNetworks: config.supportedNetworks as string[],
      currencyCode: clientConfig.currency,
      total: { label: "OpenClass Credits", type: "final", amount: paymentPackage.amount_usd },
    });
    session.onvalidatemerchant = async (event) => {
      try {
        if (!applePay.validateMerchant) throw new Error("Apple Pay 商户验证不可用");
        const result = await applePay.validateMerchant({
          validationUrl: event.validationURL,
          displayName: "OpenClass",
        });
        session.completeMerchantValidation(result.merchantSession);
      } catch {
        session.abort();
      }
    };
    session.onpaymentauthorized = async (event) => {
      try {
        const orderId = await createOrder("apple_pay");
        const result = await applePay.confirmOrder({
          orderId,
          token: event.payment.token,
          billingContact: event.payment.billingContact,
        });
        if (result.status !== "APPROVED") throw new Error("Apple Pay 订单未获批准");
        await captureOrder(orderId);
        session.completePayment(ApplePaySession.STATUS_SUCCESS);
      } catch (error) {
        reportError(errorMessage(error, "Apple Pay 付款失败"));
        session.completePayment(ApplePaySession.STATUS_FAILURE);
      }
    };
    session.oncancel = () => undefined;
    session.begin();
  });
  container.replaceChildren(button);
  setEligible(true);
}

async function initializeGooglePay(
  paypal: PayPalNamespace,
  paymentWindow: PaymentBrowserWindow,
  clientConfig: PayPalClientConfig,
  paymentPackage: CreditPackage,
  createOrder: (method: PayPalPaymentMethod) => Promise<string>,
  captureOrder: (orderId: string) => Promise<void>,
  isDisposed: () => boolean,
  setEligible: (eligible: boolean) => void,
  reportError: (message: string) => void
) {
  if (!paypal.Googlepay) return;
  await loadScript("openclass-google-pay-sdk", "https://pay.google.com/gp/p/js/pay.js");
  if (isDisposed() || !paymentWindow.google) return;
  const googlePay = paypal.Googlepay();
  const config = await googlePay.config();
  const allowedPaymentMethods = config.allowedPaymentMethods as unknown[];
  const merchantInfo = config.merchantInfo as Record<string, unknown>;
  if (!Array.isArray(allowedPaymentMethods)) return;

  const paymentsClient = new paymentWindow.google.payments.api.PaymentsClient({
    environment: clientConfig.mode === "live" ? "PRODUCTION" : "TEST",
    paymentDataCallbacks: {
      onPaymentAuthorized: async (paymentData: Record<string, unknown>) => {
        try {
          const orderId = await createOrder("google_pay");
          const result = await googlePay.confirmOrder({
            orderId,
            paymentMethodData: paymentData.paymentMethodData,
          });
          if (result.status === "PAYER_ACTION_REQUIRED" && googlePay.initiatePayerAction) {
            await googlePay.initiatePayerAction({ orderId });
          } else if (result.status !== "APPROVED") {
            throw new Error("Google Pay 订单未获批准");
          }
          await captureOrder(orderId);
          return { transactionState: "SUCCESS" };
        } catch (error) {
          reportError(errorMessage(error, "Google Pay 付款失败"));
          return {
            transactionState: "ERROR",
            error: {
              intent: "PAYMENT_AUTHORIZATION",
              message: errorMessage(error, "Google Pay 付款失败"),
            },
          };
        }
      },
    },
  });
  const baseRequest = { apiVersion: 2, apiVersionMinor: 0, allowedPaymentMethods };
  const readiness = await paymentsClient.isReadyToPay(baseRequest);
  if (!readiness.result) return;

  const paymentDataRequest = {
    ...baseRequest,
    merchantInfo,
    callbackIntents: ["PAYMENT_AUTHORIZATION"],
    transactionInfo: {
      currencyCode: clientConfig.currency,
      totalPriceStatus: "FINAL",
      totalPrice: paymentPackage.amount_usd,
    },
  };
  const button = paymentsClient.createButton({
    buttonType: "pay",
    buttonColor: "black",
    buttonLocale: "zh",
    buttonSizeMode: "fill",
    allowedPaymentMethods,
    onClick: () => {
      void paymentsClient.loadPaymentData(paymentDataRequest).catch((error) => {
        if ((error as { statusCode?: string })?.statusCode !== "CANCELED") {
          reportError(errorMessage(error, "Google Pay 付款失败"));
        }
      });
    },
  });
  const container = document.getElementById("google-pay-button-container");
  if (!container) return;
  container.replaceChildren(button);
  setEligible(true);
}
