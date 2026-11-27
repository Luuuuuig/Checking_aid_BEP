"""The client of an Anthropic model in Microsoft Foundry: the Anthropic client with the SDK's AnthropicFoundry behind it.

The request, the streaming, the token count and the cost are the parent's (aid/readers/llm_client.py). Only the SDK client
differs, and the answer names the deployment, as "foundry:<deployment>", not the model the service reports.

With its debug log on (ANTHROPIC_LOG=debug, or the root logger at DEBUG), anthropic 2.3.0 logs the options of every request at the
logger SDK_LOG, and for Foundry those hold the key in the headers x-api-key and api-key. The client therefore puts a filter on that
logger (KeyHidden), once per key, which writes KEY_HIDDEN in the key's place in each record's message.

The SDK client runs over the SDK's http client with follow_redirects=False, for the answer and for the token count alike. The
SDK's default follows redirects and would send the key headers and the request body to the place a redirect names, even over
plain http. Here a redirect is answered as the SDK answers any status that is not a success (APIStatusError, with the status),
so it is a failure of the service, and the key and the document go to the configured address only.
"""
import logging
from dataclasses import replace
from decimal import Decimal

from aid.readers import llm_client
from aid.readers.llm_client import AnthropicClient, LlmRequest, LlmResponse, ReaderError

NO_FOUNDRY_LIBRARY = "this Python's anthropic package has no client for Foundry: install the version in aid/requirements.txt"
SDK_LOG = "anthropic._base_client"                 # the logger of the SDK's request options, which hold the key
KEY_HIDDEN = "<key>"                               # what that log shows in the key's place


class KeyHidden(logging.Filter):
    """A filter of the SDK's request log that writes KEY_HIDDEN in the place of `key` in the finished message of each record."""

    def __init__(self, key: str):
        super().__init__()
        self.key = key

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.key in message:
            record.msg, record.args = message.replace(self.key, KEY_HIDDEN), None
        return True


def hide_key(key: str) -> None:
    """Put a KeyHidden of `key` on the SDK's request log, unless one is there already (or the key is empty)."""
    log = logging.getLogger(SDK_LOG)
    if key and not any(isinstance(log_filter, KeyHidden) and log_filter.key == key for log_filter in log.filters):
        log.addFilter(KeyHidden(key))


class FoundryClient(AnthropicClient):
    """An Anthropic model deployed in Microsoft Foundry. `deployment` is the deployment's name, which is the model of the
    request. The key comes from the caller and is never logged or put in an error. Give exactly one of `resource` (the name of
    the Foundry resource) and `base_url` (its full address, such as https://<resource>.services.ai.azure.com/anthropic). anthropic
    2.3.0 also reads the two from the process environment (ANTHROPIC_FOUNDRY_RESOURCE, ANTHROPIC_FOUNDRY_BASE_URL) and takes one
    that is there, even empty, as set: beside the other one given here, it refuses the pair at every request.

    Never call copy, with_options or with_middleware on the SDK client: in anthropic 2.3.0 they fail when the environment
    variable ANTHROPIC_FOUNDRY_RESOURCE is set. The client also cannot see whether a deployment is hosted on Azure: that is
    for the caller to know. `answered_model` is the model that the answer to the last call named, before it is renamed (also the
    answer a ReaderError carries); None before the first call and after a call that brought no answer."""

    def __init__(self, deployment: str, api_key: str, resource: str | None = None, base_url: str | None = None,
                 effort: str = "medium", price_per_mtok: tuple[Decimal, Decimal] | None = None):
        if (resource is None) == (base_url is None):
            raise ValueError("give exactly one of resource and base_url")
        super().__init__(deployment, effort, price_per_mtok)
        self.name = f"foundry:{deployment}"
        self._api_key = api_key
        self._resource = resource
        self._base_url = base_url
        self.answered_model: str | None = None

    def complete(self, req: LlmRequest) -> LlmResponse:
        """The parent's answer, with the model named by the deployment. This holds for the answer in a ReaderError too."""
        self.answered_model = None
        try:
            response = super().complete(req)
        except ReaderError as error:
            if error.response is not None:
                self.answered_model = error.response.model
                error.response = replace(error.response, model=self.name)
            raise
        self.answered_model = response.model
        return replace(response, model=self.name)

    def _client(self):
        if self._sdk is None:
            sdk = llm_client.anthropic
            if sdk is None or not hasattr(sdk, "AnthropicFoundry"):
                raise ReaderError(NO_FOUNDRY_LIBRARY)
            where = {"resource": self._resource} if self._resource is not None else {"base_url": self._base_url}
            hide_key(self._api_key)                  # before the first request, whose options the SDK may log
            # The SDK's own http client follows redirects, with the key headers and the body: this one follows none.
            http_client = sdk.DefaultHttpxClient(follow_redirects=False)
            # One attempt is one request: the SDK would send it again on its own after a timeout, a 408, 409, 429 or 5xx (two more
            # times by default), which the runner's count of the attempts (aid/runner.py, _run_cases) would not see.
            # The SDK reads a Retry-After header only to time those retries, so with none it waits for nothing.
            self._sdk = sdk.AnthropicFoundry(api_key=self._api_key, http_client=http_client, max_retries=0, **where)
        return self._sdk
