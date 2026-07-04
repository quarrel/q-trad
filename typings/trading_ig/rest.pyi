from collections.abc import Mapping

class _Session:
    headers: Mapping[str, str]

class IGService:
    session: _Session

    def __init__(
        self,
        username: str,
        password: str,
        api_key: str,
        acc_type: str = "demo",
        acc_number: str | None = None,
        session: object | None = None,
        return_dataframe: bool = False,
        return_munch: bool = False,
        retryer: object | None = None,
        use_rate_limiter: bool = False,
    ) -> None: ...
    def create_session(
        self,
        session: object | None = None,
        encryption: bool = False,
        version: str = "2",
    ) -> object: ...
    def search_markets(self, search_term: str, session: object | None = None) -> object: ...
    def fetch_market_by_epic(self, epic: str, session: object | None = None) -> object: ...
    def fetch_historical_prices_by_epic_and_date_range(
        self,
        epic: str,
        resolution: str,
        start_date: str,
        end_date: str,
        session: object | None = None,
        format: object | None = None,
        version: str = "2",
    ) -> object: ...
    def logout(self, session: object | None = None) -> object: ...
