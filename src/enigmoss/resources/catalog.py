"""``client.payment_methods``, ``client.payout_methods`` and ``client.exchange_rates``."""

from __future__ import annotations

import builtins

from enigmoss import _validate as v
from enigmoss._http import Endpoint
from enigmoss._options import RequestOptions
from enigmoss.models.catalog import ExchangeRate, PaymentMethod, PayoutMethod, PayoutMethodDetails
from enigmoss.resources._base import Resource

__all__ = ["ExchangeRatesResource", "PaymentMethodsResource", "PayoutMethodsResource"]

_PAYMENT_METHODS = Endpoint.get("/payment-methods", "any")
_DISCOUNT = Endpoint.patch("/payment-methods/{currency_code}/{network_code}", "payment")
_PAYOUT_METHODS = Endpoint.get("/payout-methods", "any")
_PAYOUT_METHOD = Endpoint.get("/payout-methods/{currency_code}/{network_code}", "any")
_RATES = Endpoint.get("/exchange-rates", "any")


class PaymentMethodsResource(Resource):
    """Currencies and networks customers can pay with."""

    __slots__ = ()

    def list(self, *, options: RequestOptions | None = None) -> builtins.list[PaymentMethod]:
        """Returns the payment methods of the project. ``GET /api/v4/payment-methods``, either key.

        Raises:
            EnigmossError: The request failed.
        """
        return self._all(_PAYMENT_METHODS, PaymentMethod._parse, options=options)

    def update_discount(
        self,
        currency_code: str,
        network_code: str,
        *,
        discount_percent: int,
        options: RequestOptions | None = None,
    ) -> PaymentMethod:
        """Sets the discount (positive) or surcharge (negative) of a method, -99..90 percent.

        ``PATCH /api/v4/payment-methods/{currency}/{network}``, payment key, ``payment_settings``.

        Raises:
            NotFoundError: The project has no such method.
            EnigmossError: Any other failure.
        """
        return self._one(
            _DISCOUNT,
            PaymentMethod._parse,
            path_params={"currency_code": currency_code, "network_code": network_code},
            body={"discount_percent": v.int_range(discount_percent, "discount_percent", -99, 90)},
            options=options,
        )


class PayoutMethodsResource(Resource):
    """Currencies and networks payouts can be sent to."""

    __slots__ = ()

    def list(self, *, options: RequestOptions | None = None) -> builtins.list[PayoutMethod]:
        """Returns the payout methods with limits and fees. ``GET /api/v4/payout-methods``, either key.

        Raises:
            EnigmossError: The request failed.
        """
        return self._all(_PAYOUT_METHODS, PayoutMethod._parse, options=options)

    def get(
        self, currency_code: str, network_code: str, *, options: RequestOptions | None = None
    ) -> PayoutMethodDetails:
        """Returns limits, precision and priority fees of one method.

        ``GET /api/v4/payout-methods/{currency}/{network}``, either key.

        Raises:
            NotFoundError: No such method.
            EnigmossError: Any other failure.
        """
        return self._one(
            _PAYOUT_METHOD,
            PayoutMethodDetails._parse,
            path_params={"currency_code": currency_code, "network_code": network_code},
            options=options,
        )


class ExchangeRatesResource(Resource):
    """Exchange rates."""

    __slots__ = ()

    def list(
        self, *, from_: str | None = None, options: RequestOptions | None = None
    ) -> builtins.list[ExchangeRate]:
        """Returns exchange rates. ``GET /api/v4/exchange-rates``, either key.

        Args:
            from_: Only rates from this currency (the wire parameter ``from``).
            options: Per-call overrides.

        Raises:
            EnigmossError: The request failed.
        """
        return self._all(_RATES, ExchangeRate._parse, query={"from": from_}, options=options)
