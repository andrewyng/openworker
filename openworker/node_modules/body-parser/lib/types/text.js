/*!
 * body-parser
 * Copyright(c) 2014-2015 Douglas Christopher Wilson
 * MIT Licensed
 */

'use strict'

/**
 * Module dependencies.
 */

const debug = require('debug')('body-parser:text')
const read = require('../read')
const { normalizeOptions, passthrough } = require('../utils')

/**
 * Module exports.
 */

module.exports = text

/**
 * Create a middleware to parse text bodies.
 *
 * @param {Object} [options]
 * @returns {Function}
 * @public
 */
function text (options) {
  const normalizedOptions = normalizeOptions(options, 'text/plain')

  return function textParser (req, res, next) {
    read(req, res, next, passthrough, debug, normalizedOptions)
  }
}
