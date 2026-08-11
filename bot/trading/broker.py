from abc import ABC, abstractmethod


class Broker(ABC):
    @abstractmethod
    def get_account(self):
        ...

    @abstractmethod
    def get_pricing(self, symbol):
        ...

    @abstractmethod
    def place_order(self, order):
        ...

    @abstractmethod
    def get_positions(self):
        ...

    @abstractmethod
    def close_position(self, symbol, side):
        ...
