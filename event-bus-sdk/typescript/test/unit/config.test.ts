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

/** Unit tests for environment-driven configuration (PRD §7b / §7g). */

import {
  BROKERS_ENV,
  SCHEMA_REGISTRY_ENV,
  eventBusConfigFromEnv,
} from '../../src/config';

describe('eventBusConfigFromEnv', () => {
  const saved = { ...process.env };

  afterEach(() => {
    process.env = { ...saved };
  });

  it('reads the brokers and registry URL from the environment', () => {
    process.env[BROKERS_ENV] = 'host-a:9092,host-b:9092';
    process.env[SCHEMA_REGISTRY_ENV] = 'http://registry:8081';
    const config = eventBusConfigFromEnv();
    expect(config.brokers).toEqual(['host-a:9092', 'host-b:9092']);
    expect(config.schemaRegistryUrl).toBe('http://registry:8081');
  });

  it('strips a trailing slash from the registry URL', () => {
    process.env[BROKERS_ENV] = 'host:9092';
    process.env[SCHEMA_REGISTRY_ENV] = 'http://registry:8081/';
    expect(eventBusConfigFromEnv().schemaRegistryUrl).toBe(
      'http://registry:8081',
    );
  });

  it('throws loudly when the brokers variable is unset', () => {
    delete process.env[BROKERS_ENV];
    process.env[SCHEMA_REGISTRY_ENV] = 'http://registry:8081';
    expect(() => eventBusConfigFromEnv()).toThrow(
      new RegExp(`${BROKERS_ENV} is unset`),
    );
  });

  it('throws loudly when the registry variable is unset', () => {
    process.env[BROKERS_ENV] = 'host:9092';
    delete process.env[SCHEMA_REGISTRY_ENV];
    expect(() => eventBusConfigFromEnv()).toThrow(
      new RegExp(`${SCHEMA_REGISTRY_ENV} is unset`),
    );
  });
});
