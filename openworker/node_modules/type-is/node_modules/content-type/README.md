# content-type

[![NPM version][npm-image]][npm-url]
[![NPM downloads][downloads-image]][downloads-url]
[![Build status][build-image]][build-url]
[![Build coverage][coverage-image]][coverage-url]
[![License][license-image]][license-url]

Create and parse HTTP `Content-Type` header.

## Installation

```sh
npm install content-type
```

## API

```js
const contentType = require("content-type");
```

### contentType.parse(string, options?)

```js
const obj = contentType.parse("image/svg+xml; charset=utf-8");
```

Parse a `Content-Type` header. This will return an object with the following properties (examples are shown for the string `'image/svg+xml; charset=utf-8'`):

- `type`: The media type. Example: `'image/svg+xml'`.
- `parameters`: An object of the parameters in the media type (parameter name is always lower case). Example: `{charset: 'utf-8'}`.

The parser is lenient and does not error. You should validate `type` and `parameters` before trusting them.

#### Options

- `parameters` (default: `true`): Set to `false` to skip parameters.

### contentType.format(obj)

```js
const str = contentType.format({
  type: "image/svg+xml",
  parameters: { charset: "utf-8" },
});
```

Format an object into a `Content-Type` header. This will return a string of the content type for the given object with the following properties (examples are shown that produce the string `'image/svg+xml; charset=utf-8'`):

- `type`: The media type. Example: `'image/svg+xml'`.
- `parameters`: An optional object of the parameters in the media type. Example: `{charset: 'utf-8'}`.

Throws a `TypeError` if the object contains an invalid type or parameter names.

## License

[MIT](LICENSE)

[npm-image]: https://img.shields.io/npm/v/content-type
[npm-url]: https://npmjs.org/package/content-type
[downloads-image]: https://img.shields.io/npm/dm/content-type
[downloads-url]: https://npmjs.org/package/content-type
[build-image]: https://img.shields.io/github/actions/workflow/status/jshttp/content-type/ci.yml?branch=master
[build-url]: https://github.com/jshttp/content-type/actions/workflows/ci.yml?query=branch%3Amaster
[coverage-image]: https://img.shields.io/codecov/c/gh/jshttp/content-type
[coverage-url]: https://codecov.io/gh/jshttp/content-type
[license-image]: http://img.shields.io/npm/l/content-type.svg?style=flat
[license-url]: LICENSE
