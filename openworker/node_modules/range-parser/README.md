# range-parser

[![NPM Version][npm-version-image]][npm-url]
[![NPM Downloads][npm-downloads-image]][npm-url]
[![Node.js Version][node-image]][node-url]
[![Build Status][ci-image]][ci-url]
[![Test Coverage][coveralls-image]][coveralls-url]

Range header field parser.

## Installation

This is a [Node.js](https://nodejs.org/en/) module available through the
[npm registry](https://www.npmjs.com/). Installation is done using the
[`npm install` command](https://docs.npmjs.com/getting-started/installing-npm-packages-locally):

```sh
$ npm install range-parser
```

## API

<!-- eslint-disable no-unused-vars -->

```js
var parseRange = require('range-parser')
```

### parseRange(size, header, options)

Parse the given `header` string where `size` is the size of the selected
representation that is to be partitioned into subranges. An array of subranges
will be returned or negative numbers indicating an error parsing.

  * `-2` signals a malformed header string
  * `-1` signals an unsatisfiable range

<!-- eslint-disable no-undef -->

```js
// parse header from request
var subranges = parseRange(size, req.headers.range)

// the type of the subranges
if (subranges.type === 'bytes') {
  // the ranges
  subranges.forEach(function (r) {
    // do something with r.start and r.end
  })
}
```

#### Options

These properties are accepted in the options object.

##### combine

Specifies if overlapping & adjacent subranges should be combined, defaults to
`false`. When `true`, ranges will be combined and returned as if they were
specified that way in the header.

<!-- eslint-disable no-undef -->

```js
parseRange(100, 'bytes=50-55,0-10,5-10,56-60', { combine: true })
// => [
//      { start: 0,  end: 10 },
//      { start: 50, end: 60 }
//    ]
```

## License

[MIT](LICENSE)

[ci-image]: https://badgen.net/github/checks/jshttp/range-parser/master?label=ci
[ci-url]: https://github.com/jshttp/range-parser/actions/workflows/ci.yml
[coveralls-image]: https://badgen.net/coveralls/c/github/jshttp/range-parser/master
[coveralls-url]: https://coveralls.io/r/jshttp/range-parser?branch=master
[node-image]: https://badgen.net/npm/node/range-parser
[node-url]: https://nodejs.org/en/download
[npm-downloads-image]: https://badgen.net/npm/dm/range-parser
[npm-url]: https://npmjs.org/package/range-parser
[npm-version-image]: https://badgen.net/npm/v/range-parser
