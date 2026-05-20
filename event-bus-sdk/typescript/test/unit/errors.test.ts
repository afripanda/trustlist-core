// Copyright 2026 The TrustList Foundation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * Unit tests for the typed error hierarchy (PRD §7b).
 *
 * The hierarchy mirrors the Python SDK's so both surface the same failure
 * taxonomy; back-pressure being its own catchable type is the load-bearing §7b
 * requirement.
 */

import {
  BackPressureError,
  EventBusError,
  ProduceError,
  SchemaRegistryError,
  SchemaValidationError,
} from '../../src/errors';

describe('event-bus error hierarchy', () => {
  it.each([
    ['BackPressureError', new BackPressureError('q full')],
    ['ProduceError', new ProduceError('broker NAK')],
    ['SchemaValidationError', new SchemaValidationError('bad payload')],
    ['SchemaRegistryError', new SchemaRegistryError('registry down')],
  ])('%s is an EventBusError', (_name, error) => {
    expect(error).toBeInstanceOf(EventBusError);
    expect(error).toBeInstanceOf(Error);
  });

  it('reports each subclass under its own name', () => {
    expect(new BackPressureError('x').name).toBe('BackPressureError');
    expect(new ProduceError('x').name).toBe('ProduceError');
    expect(new SchemaValidationError('x').name).toBe('SchemaValidationError');
    expect(new SchemaRegistryError('x').name).toBe('SchemaRegistryError');
  });

  it('lets a caller catch back-pressure distinctly from other failures', () => {
    const isBackPressure = (error: unknown): boolean =>
      error instanceof BackPressureError;
    expect(isBackPressure(new BackPressureError('x'))).toBe(true);
    expect(isBackPressure(new ProduceError('x'))).toBe(false);
  });
});
