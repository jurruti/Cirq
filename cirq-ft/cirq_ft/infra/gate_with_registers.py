# Copyright 2023 The Cirq Developers
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import abc
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union, overload

import attr
import cirq
import numpy as np

assert sys.version_info > (3, 6), "https://docs.python.org/3/whatsnew/3.6.html#whatsnew36-pep468"


@attr.frozen
class Register:
    """A quantum register used to define the input/output API of a `cirq_ft.GateWithRegister`

    Args:
        name: The string name of the register
        bitsize: The number of (qu)bits in the register.
    """

    name: str
    bitsize: int

    def __repr__(self):
        return f'cirq_ft.Register("{self.name}", {self.bitsize})'


class Registers:
    """An ordered collection of `cirq_ft.Register`.

    Args:
        registers: an iterable of the contained `cirq_ft.Register`.
    """

    def __init__(self, registers: Iterable[Register]):
        self._registers = tuple(registers)
        self._register_dict = {r.name: r for r in self._registers}
        if len(self._registers) != len(self._register_dict):
            raise ValueError("Please provide unique register names.")

    def __repr__(self):
        return f'cirq_ft.Registers({self._registers})'

    @property
    def bitsize(self) -> int:
        return sum(reg.bitsize for reg in self)

    @classmethod
    def build(cls, **registers: int) -> 'Registers':
        return cls(Register(name=k, bitsize=v) for k, v in registers.items())

    @overload
    def __getitem__(self, key: int) -> Register:
        pass

    @overload
    def __getitem__(self, key: str) -> Register:
        pass

    @overload
    def __getitem__(self, key: slice) -> 'Registers':
        pass

    def __getitem__(self, key):
        if isinstance(key, slice):
            return Registers(self._registers[key])
        elif isinstance(key, int):
            return self._registers[key]
        elif isinstance(key, str):
            return self._register_dict[key]
        else:
            raise IndexError(f"key {key} must be of the type str/int/slice.")

    def __contains__(self, item: str) -> bool:
        return item in self._register_dict

    def __iter__(self):
        yield from self._registers

    def __len__(self) -> int:
        return len(self._registers)

    def split_qubits(self, qubits: Sequence[cirq.Qid]) -> Dict[str, Sequence[cirq.Qid]]:
        qubit_regs = {}
        base = 0
        for reg in self:
            qubit_regs[reg.name] = qubits[base : base + reg.bitsize]
            base += reg.bitsize
        return qubit_regs

    def merge_qubits(self, **qubit_regs: Union[cirq.Qid, Sequence[cirq.Qid]]) -> List[cirq.Qid]:
        ret: List[cirq.Qid] = []
        for reg in self:
            assert reg.name in qubit_regs, "All qubit registers must pe present"
            qubits = qubit_regs[reg.name]
            qubits = [qubits] if isinstance(qubits, cirq.Qid) else qubits
            assert (
                len(qubits) == reg.bitsize
            ), f"{reg.name} register must of length {reg.bitsize} but is of length {len(qubits)}"
            ret += qubits
        return ret

    def get_named_qubits(self) -> Dict[str, List[cirq.Qid]]:
        def qubits_for_reg(name: str, bitsize: int):
            return (
                [cirq.NamedQubit(f"{name}")]
                if bitsize == 1
                else cirq.NamedQubit.range(bitsize, prefix=name)
            )

        return {reg.name: qubits_for_reg(reg.name, reg.bitsize) for reg in self}

    def __eq__(self, other) -> bool:
        return self._registers == other._registers

    def __hash__(self):
        return hash(self._registers)


@attr.frozen
class SelectionRegister(Register):
    """Register used to represent SELECT register for various LCU methods.

    `SelectionRegister` extends the `Register` class to store the iteration length
    corresponding to that register along with its size.
    """

    iteration_length: int = attr.field()

    @iteration_length.validator
    def validate_iteration_length(self, attribute, value):
        if not (0 <= value <= 2**self.bitsize):
            raise ValueError(f'iteration length must be in range [0, 2^{self.bitsize}]')

    def __repr__(self) -> str:
        return f'cirq_ft.SelectionRegister("{self.name}", {self.bitsize}, {self.iteration_length})'


class SelectionRegisters(Registers):
    """Registers used to represent SELECT registers for various LCU methods.

    LCU methods often make use of coherent for-loops via UnaryIteration, iterating over a range
    of values stored as a superposition over the `SELECT` register. The `SelectionRegisters` class
    is used to represent such SELECT registers. In particular, it provides two additional features
    on top of the regular `Registers` class:

    - For each selection register, we store the iteration length corresponding to that register
        along with its size.
    - We provide a default way of "flattening out" a composite index represented by a tuple of
        values stored in multiple input selection registers to a single integer that can be used
        to index a flat target register.
    """

    def __init__(self, registers: Iterable[SelectionRegister]):
        super().__init__(registers)
        self.iteration_lengths = tuple([reg.iteration_length for reg in registers])
        self._suffix_prod = np.multiply.accumulate(self.iteration_lengths[::-1])[::-1]
        self._suffix_prod = np.append(self._suffix_prod, [1])

    def to_flat_idx(self, *selection_vals: int) -> int:
        """Flattens a composite index represented by a Tuple[int, ...] to a single output integer.

        For example:

        1) We can flatten a 2D for-loop as follows
        >>> N, M = 10, 20
        >>> flat_indices = set()
        >>> for x in range(N):
        ...     for y in range(M):
        ...         flat_idx = x * M + y
        ...         flat_indices.add(flat_idx)
        >>> assert len(flat_indices) == N * M

        2) Similarly, we can flatten a 3D for-loop as follows
        >>> N, M, L = 10, 20, 30
        >>> flat_indices = set()
        >>> for x in range(N):
        ...     for y in range(M):
        ...         for z in range(L):
        ...             flat_idx = x * M * L + y * L + z
        ...             flat_indices.add(flat_idx)
        >>> assert len(flat_indices) == N * M * L

        This is a general version of the mapping function described in Eq.45 of
        https://arxiv.org/abs/1805.03662
        """
        assert len(selection_vals) == len(self)
        return sum(v * self._suffix_prod[i + 1] for i, v in enumerate(selection_vals))

    @property
    def total_iteration_size(self) -> int:
        return int(np.product(self.iteration_lengths))

    @classmethod
    def build(cls, **registers: Union[int, Tuple[int, int]]) -> 'SelectionRegisters':
        reg_dict: Dict[str, Tuple[int, int]] = {
            k: v if isinstance(v, tuple) else (v, 2**v) for k, v in registers.items()
        }
        return SelectionRegisters(
            [
                SelectionRegister(name=k, bitsize=v[0], iteration_length=v[1])
                for k, v in reg_dict.items()
            ]
        )

    @overload
    def __getitem__(self, key: int) -> SelectionRegister:
        pass

    @overload
    def __getitem__(self, key: str) -> SelectionRegister:
        pass

    @overload
    def __getitem__(self, key: slice) -> 'SelectionRegisters':
        pass

    def __getitem__(self, key):
        if isinstance(key, slice):
            return SelectionRegisters(self._registers[key])
        elif isinstance(key, int):
            return self._registers[key]
        elif isinstance(key, str):
            return self._register_dict[key]
        else:
            raise IndexError(f"key {key} must be of the type str/int/slice.")

    def __repr__(self) -> str:
        return f'cirq_ft.SelectionRegisters({self._registers})'


class GateWithRegisters(cirq.Gate, metaclass=abc.ABCMeta):
    """`cirq.Gate`s extension with support for composite gates acting on multiple qubit registers.

    Though Cirq was nominally designed for circuit construction for near-term devices the core
    concept of the `cirq.Gate`, a programmatic representation of an operation on a state without
    a complete qubit address specification, can be leveraged to describe more abstract algorithmic
    primitives. To define composite gates, users derive from `cirq.Gate` and implement the
    `_decompose_` method that yields the sub-operations provided a flat list of qubits.

    This API quickly becomes inconvenient when defining operations that act on multiple qubit
    registers of variable sizes. Cirq-FT extends the `cirq.Gate` idea by introducing a new abstract
    base class `cirq_ft.GateWithRegisters` containing abstract methods `registers` and optional
    method `decompose_from_registers` that provides an overlay to the Cirq flat address API.

    As an example, in the following code snippet we use the `cirq_ft.GateWithRegisters` to
    construct a multi-target controlled swap operation:

    >>> import attr
    >>> import cirq
    >>> import cirq_ft
    >>>
    >>> @attr.frozen
    ... class MultiTargetCSwap(cirq_ft.GateWithRegisters):
    ...     bitsize: int
    ...
    ...     @property
    ...     def registers(self) -> cirq_ft.Registers:
    ...         return cirq_ft.Registers.build(ctrl=1, x=self.bitsize, y=self.bitsize)
    ...
    ...     def decompose_from_registers(self, context, ctrl, x, y) -> cirq.OP_TREE:
    ...         yield [cirq.CSWAP(*ctrl, qx, qy) for qx, qy in zip(x, y)]
    ...
    >>> op = MultiTargetCSwap(2).on_registers(
    ...     ctrl=[cirq.q('ctrl')],
    ...     x=cirq.NamedQubit.range(2, prefix='x'),
    ...     y=cirq.NamedQubit.range(2, prefix='y'),
    ... )
    >>> print(cirq.Circuit(op))
    ctrl: ───MultiTargetCSwap───
             │
    x0: ─────x──────────────────
             │
    x1: ─────x──────────────────
             │
    y0: ─────y──────────────────
             │
    y1: ─────y──────────────────"""

    @property
    @abc.abstractmethod
    def registers(self) -> Registers:
        ...

    def _num_qubits_(self) -> int:
        return self.registers.bitsize

    def decompose_from_registers(
        self, *, context: cirq.DecompositionContext, **quregs: Sequence[cirq.Qid]
    ) -> cirq.OP_TREE:
        return NotImplemented

    def _decompose_with_context_(
        self, qubits: Sequence[cirq.Qid], context: Optional[cirq.DecompositionContext] = None
    ) -> cirq.OP_TREE:
        qubit_regs = self.registers.split_qubits(qubits)
        if context is None:
            context = cirq.DecompositionContext(cirq.ops.SimpleQubitManager())
        return self.decompose_from_registers(context=context, **qubit_regs)

    def _decompose_(self, qubits: Sequence[cirq.Qid]) -> cirq.OP_TREE:
        return self._decompose_with_context_(qubits)

    def on_registers(self, **qubit_regs: Union[cirq.Qid, Sequence[cirq.Qid]]) -> cirq.Operation:
        return self.on(*self.registers.merge_qubits(**qubit_regs))

    def _circuit_diagram_info_(self, args: cirq.CircuitDiagramInfoArgs) -> cirq.CircuitDiagramInfo:
        """Default diagram info that uses register names to name the boxes in multi-qubit gates.

        Descendants can override this method with more meaningful circuit diagram information.
        """
        wire_symbols = []
        for reg in self.registers:
            wire_symbols += [reg.name] * reg.bitsize

        wire_symbols[0] = self.__class__.__name__
        return cirq.CircuitDiagramInfo(wire_symbols=wire_symbols)
