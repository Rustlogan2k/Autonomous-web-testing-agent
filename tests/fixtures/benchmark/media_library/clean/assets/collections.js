/* Reelhouse collections page: a read-only view of the shelf grouped by collection.
   A collection's list of recordings is built when it is opened and removed when it is
   closed; which lists are open is not remembered. */
(function () {
  'use strict';

  var Library = Reelhouse.Library;
  var esc = Reelhouse.escapeHtml;
  var holder = document.getElementById('crates');
  var opened = {};

  function membersOf(value) {
    return Library.all().filter(function (record) { return record.collection === value; });
  }

  function render() {
    holder.innerHTML = Reelhouse.COLLECTIONS.map(function (collection) {
      var members = membersOf(collection.value);
      var isOpen = Boolean(opened[collection.value]);
      var list = '';
      if (isOpen) {
        list = members.length
          ? '<ol class="crate-list">' + members.map(function (record) {
              return '<li><span class="crate-ref">' + record.ref + '</span> ' + esc(record.title) +
                ' <span class="crate-time">' + Reelhouse.runningTime(record.seconds) + '</span></li>';
            }).join('') + '</ol>'
          : '<p class="crate-empty">Nothing is filed here at the moment.</p>';
      }
      return '<section class="crate">' +
        '<h2>' + collection.label + '</h2>' +
        '<p class="crate-note">' + collection.note + '</p>' +
        '<p class="crate-count">' + members.length + (members.length === 1 ? ' recording' : ' recordings') + '</p>' +
        '<button type="button" class="key" id="list-' + collection.value + '">' +
          (isOpen ? 'Hide recordings' : 'List recordings') + '</button>' +
        list +
        '</section>';
    }).join('');
  }

  holder.addEventListener('click', function (event) {
    var button = event.target.closest('button');
    if (!button) { return; }
    var value = button.id.replace('list-', '');
    opened[value] = !opened[value];
    render();
  });

  window.addEventListener('pageshow', function (event) {
    if (event.persisted) {
      Library.reopen();
      render();
    }
  });

  render();
}());
