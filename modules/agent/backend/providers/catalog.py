"""Connection/model settings; provider profiles and user overrides remain separate."""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator, model_validator
from talyx_sdk import ContractError

from ..model_settings import ConnectionFields, ModelConfig
from ..usage import Price
from .presets import OFFICIAL_SITES
from .thinking import plan, schema

# Bundled LangChain profiles only fill fields models.dev lacks for a model.
PROFILES = {
    provider: importlib.import_module(f"langchain_{package}.data._profiles")._PROFILES
    for provider, package in (("openai", "openai"), ("anthropic", "anthropic"), ("google", "google_genai"))
}
# models.dev lists each official site of a provider as its own entry.
MODELS_DEV_SITES = {("moonshotai", "domestic"): "moonshotai-cn", ("zhipuai", "international"): "zai"}
# A relay or local model id found under several providers takes the original maker's entry.
CANONICAL = ("openai", "anthropic", "google", "xai", "deepseek", "moonshotai", "zhipuai", "zai", "moonshotai-cn")


class ConnectionConfig(ConnectionFields):
    secretRef: str | None = Field(default=None, min_length=1)

    @field_validator("baseUrl")
    @classmethod
    def check_url(cls, value):
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or
                url.query or url.fragment or url.port is not None and not 0 < url.port < 65536):
            raise ValueError("A connection requires an HTTP(S) base URL without embedded credentials")
        return value

    @model_validator(mode="after")
    def check(self):
        if self.kind == "relay" and self.protocol not in {"openai", "anthropic"}:
            raise ValueError("Relay protocol must be OpenAI or Anthropic")
        if self.kind == "local" and self.protocol not in {"openai", "ollama"}:
            raise ValueError("Local protocol must be Ollama or OpenAI (LM Studio)")
        if self.kind != "local" and self.secretRef is None:
            raise ValueError("This connection requires a backend credential reference")
        if self.kind == "official" and self.providerId in {"moonshotai", "zhipuai", "alibaba"}:
            if self.site is None:
                self.site = next((site for (provider, site), base in OFFICIAL_SITES.items()
                                  if provider == self.providerId and self.baseUrl.rstrip("/") == base), None)
            if self.site is None or self.baseUrl.rstrip("/") != OFFICIAL_SITES[(self.providerId, self.site)]:
                raise ValueError("Choose this provider's domestic or international official site")
        return self


def validate(model, value):
    try:
        return model.model_validate(value)
    except (ValidationError, ValueError) as error:
        fields = [".".join(map(str, item["loc"])) for item in error.errors()] if isinstance(error, ValidationError) else []
        raise ContractError("invalid_input", "Invalid Agent settings", {"fields": fields}) from error


class ProviderCatalog:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, config TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS models (id TEXT PRIMARY KEY, config TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS tests (model_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS prices (provider TEXT NOT NULL, model TEXT NOT NULL, value TEXT NOT NULL,
             PRIMARY KEY(provider,model));
          CREATE TABLE IF NOT EXISTS profiles (provider TEXT NOT NULL, model TEXT NOT NULL, value TEXT NOT NULL,
             PRIMARY KEY(provider,model));
          CREATE INDEX IF NOT EXISTS profiles_by_model ON profiles(model);
          CREATE TABLE IF NOT EXISTS profile_source (id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL, refreshed REAL NOT NULL);
        """)
        self.db.commit()

    def connection(self, connection_id):
        row = self.db.execute("SELECT config FROM connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise ContractError("target_gone", "Model connection no longer exists")
        return ConnectionConfig.model_validate_json(row[0])

    def model(self, model_id):
        row = self.db.execute("SELECT config FROM models WHERE id=?", (model_id,)).fetchone()
        if row is None:
            raise ContractError("target_gone", "Model no longer exists")
        return ModelConfig.model_validate_json(row[0])

    def put_connection(self, value):
        connection = validate(ConnectionConfig, value)
        previous = self.db.execute("SELECT config FROM connections WHERE id=?", (connection.id,)).fetchone()
        if previous is not None:
            old = ConnectionConfig.model_validate_json(previous[0])
            if old.site != connection.site or old.site is not None and old.providerId != connection.providerId:
                raise ContractError("conflict", "Changing provider site requires a new connection and credential")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO connections VALUES (?,?)", (connection.id, connection.model_dump_json()))
        return connection.model_dump()

    def put_model(self, value):
        model = validate(ModelConfig, value)
        self.connection(model.connectionId)
        profile = self.resolve(model=model)
        if model.price is not None:
            try:
                price = Price(**model.price)
                price.estimate({"input_tokens": 1, "output_tokens": 1}, {})
            except (TypeError, ValueError, ArithmeticError) as error:
                raise ContractError("invalid_input", "Invalid model price", {"field": "price"}) from error
        if model.maxOutputTokens is not None and profile["contextWindow"] is not None and model.maxOutputTokens > profile["maxOutputTokens"]:
            raise ContractError("invalid_input",
                f"Context window {profile['contextWindow']} tokens, requested output {model.maxOutputTokens} tokens. "
                f"Choose an output of at most {profile['maxOutputTokens']} tokens (provider limit or one quarter of the window).")
        if profile["contextWindow"] == 1:
            raise ContractError("invalid_input", "Context window 1 token, output reserve 1 token: no room for input.")
        if not profile["contextWindow"]:
            raise ContractError("invalid_input", "A model requires a context window", {"missingFields": ["contextWindow"]})
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO models VALUES (?,?)", (model.id, model.model_dump_json()))
        return profile

    def delete_model(self, model_id):
        self.model(model_id)
        with self.db:
            self.db.execute("DELETE FROM models WHERE id=?", (model_id,))
            self.db.execute("DELETE FROM tests WHERE model_id=?", (model_id,))

    def delete_connection(self, connection_id):
        self.connection(connection_id)
        models = [self.model(row[0]) for row in self.db.execute("SELECT id FROM models")]
        if any(model.connectionId == connection_id for model in models):
            raise ContractError("conflict", "Remove this connection's models before removing the connection")
        with self.db:
            self.db.execute("DELETE FROM connections WHERE id=?", (connection_id,))

    def list_connections(self):
        return [self.connection(row[0]).model_dump() for row in self.db.execute("SELECT id FROM connections ORDER BY id")]

    def list_models(self):
        return [self.resolve(row[0]) for row in self.db.execute("SELECT id FROM models ORDER BY id")]

    def profiles(self, provider, *, offset=0):
        profiles = dict(PROFILES[provider]) if provider in PROFILES else {}
        profiles.update({row[0]: json.loads(row[1]) for row in self.db.execute("SELECT model,value FROM profiles WHERE provider=?", (provider,))})
        items = sorted(profiles.items())
        return {"items": [{"model": name, "profile": profile} for name, profile in items[offset:offset + 25]],
                "nextOffset": offset + 25 if offset + 25 < len(items) else None}

    def fingerprint(self, model_id):
        model = self.model(model_id)
        value = {"model": model.model_dump(), "connection": self.connection(model.connectionId).model_dump()}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def known(self, provider, name):
        return (provider in PROFILES and name in PROFILES[provider] or
                self.db.execute("SELECT 1 FROM profiles WHERE provider=? AND model=?", (provider, name)).fetchone() is not None)

    def match(self, model, connection):
        """The models.dev entry describing a model: the official provider's own, else the same id under any provider."""
        if model.profileProvider is not None:
            return model.profileProvider, model.profileModel or model.model
        name = model.model
        if connection.kind == "official":
            site = (connection.providerId, connection.site)
            return MODELS_DEV_SITES[site] if site in MODELS_DEV_SITES else connection.providerId, name
        vendor, _, rest = name.partition("/")
        if rest and self.known(vendor, rest):
            return vendor, rest
        found = {row[0] for row in self.db.execute("SELECT provider FROM profiles WHERE model=?", (name,))}
        found |= {provider for provider, profiles in PROFILES.items() if name in profiles}
        for provider in (connection.providerId, *CANONICAL):
            if provider in found:
                return provider, name
        return (min(found) if found else connection.providerId), name

    def profile(self, provider, name):
        bundled = PROFILES[provider][name] if provider in PROFILES and name in PROFILES[provider] else {}
        row = self.db.execute("SELECT value FROM profiles WHERE provider=? AND model=?", (provider, name)).fetchone()
        return {**bundled, **json.loads(row[0])} if row is not None else dict(bundled)

    def thinking(self, model_id):
        """Thinking levels and the facts the runtime needs to send one natively."""
        return self._described(self.model(model_id))[3]

    def _described(self, model):
        connection = self.connection(model.connectionId)
        provider, name = self.match(model, connection)
        profile = self.profile(provider, name)
        return provider, name, profile, plan(profile, protocol=connection.protocol, provider=provider, name=name,
                                             discovered=model.thinking)

    def refreshed(self):
        row = self.db.execute("SELECT version,refreshed FROM profile_source WHERE id=1").fetchone()
        return None if row is None else {"version": row[0], "refreshedAt": row[1]}

    def resolve(self, model_id=None, *, model=None, billing=False):
        model = self.model(model_id) if model is None else model
        provider, name, profile, thinking = self._described(model)
        price_row = self.db.execute("SELECT value FROM prices WHERE provider=? AND model=?", (provider, name)).fetchone()
        price = model.price if model.price is not None else json.loads(price_row[0]) if price_row else None
        if price is not None:
            price = {**price, "multiplier": model.priceMultiplier}
        window = model.contextWindow or profile.get("max_input_tokens")
        output = None
        if window:
            limits = [max(1, window // 4)]
            if maximum := profile.get("max_output_tokens"):
                limits.append(maximum)
            if model.maxOutputTokens is not None:
                limits.append(model.maxOutputTokens)
            output = min(limits)
        return {"id": model.id, "connectionId": model.connectionId, "model": model.model,
            "profileProvider": provider, "profileModel": name, "profile": profile, "overrides": model.model_dump(),
            "contextWindow": window, "maxOutputTokens": output,
            "tools": model.tools if model.tools is not None else profile.get("tool_calling"),
            "images": model.images if model.images is not None else profile.get("image_inputs"),
            "pdf": model.pdf if model.pdf is not None else profile.get("pdf_inputs"),
            "thinkingSchema": schema(thinking), "thinkingDefault": thinking["default"],
            "price": price if billing or model.showPrice else None, "searchMode": model.searchMode}

    def test_fingerprint(self, model_id):
        model = self.model(model_id)
        connection = self.connection(model.connectionId)
        value = [model.model, model.connectionId, connection.baseUrl, connection.secretRef, connection.protocol]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()

    def save_test(self, model_id, fingerprint, result):
        if self.fingerprint(model_id) != fingerprint:
            raise ContractError("conflict", "Model settings changed during the connection test")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO tests VALUES (?,?,?)", (model_id, self.test_fingerprint(model_id), json.dumps(result)))

    def credentials_changed(self, connection_id):
        """Call after the credential authority replaces a key at the same reference."""
        self.connection(connection_id)
        model_ids = [row[0] for row in self.db.execute("SELECT id FROM models") if self.model(row[0]).connectionId == connection_id]
        with self.db:
            self.db.executemany("DELETE FROM tests WHERE model_id=?", [(model_id,) for model_id in model_ids])

    def read_test(self, model_id):
        row = self.db.execute("SELECT fingerprint,result FROM tests WHERE model_id=?", (model_id,)).fetchone()
        if row is None or row[0] != self.test_fingerprint(model_id):
            return {"state": "not_tested", "checks": {}}
        return json.loads(row[1])

    def update_profiles(self, data, *, version, refreshed):
        """models.dev data enters at a network boundary; it replaces the previous snapshot, never user overrides."""
        with self.db:
            self.db.execute("DELETE FROM profiles")
            self.db.execute("DELETE FROM prices")
            for provider, definition in data.items():
                for name, model in definition["models"].items():
                    limits = model["limit"] if "limit" in model else {}
                    modalities = model["modalities"] if "modalities" in model else {}
                    inputs = modalities["input"] if "input" in modalities else []
                    profile = {"source": "models.dev", "version": version, "name": model["name"] if "name" in model else name}
                    if "context" in limits:
                        profile["context_window"] = limits["context"]
                        profile["max_input_tokens"] = limits["input"] if "input" in limits else limits["context"]
                    if "output" in limits:
                        profile["max_output_tokens"] = limits["output"]
                    if "tool_call" in model:
                        profile["tool_calling"] = model["tool_call"]
                    if "reasoning" in model:
                        profile["reasoning_output"] = model["reasoning"]
                    if "interleaved" in model:
                        profile["interleaved"] = model["interleaved"]
                    if "reasoning_options" in model:
                        profile["reasoning_options"] = model["reasoning_options"]
                    if inputs:
                        profile["image_inputs"] = "image" in inputs
                        profile["pdf_inputs"] = "pdf" in inputs
                    self.db.execute("INSERT OR REPLACE INTO profiles VALUES (?,?,?)", (provider, name, json.dumps(profile)))
                    if "cost" not in model or "input" not in model["cost"] or "output" not in model["cost"]:
                        continue
                    cost = model["cost"]
                    price = Price(input=str(cost["input"]), output=str(cost["output"]), version=version,
                                  cache_read=str(cost["cache_read"]) if "cache_read" in cost else None,
                                  cache_write=str(cost["cache_write"]) if "cache_write" in cost else None)
                    self.db.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?)", (provider, name, json.dumps(price.__dict__)))
            self.db.execute("INSERT OR REPLACE INTO profile_source VALUES (1,?,?)", (version, refreshed))

    def close(self):
        self.db.close()
