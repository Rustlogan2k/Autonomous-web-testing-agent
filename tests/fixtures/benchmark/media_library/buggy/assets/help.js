/* Reelhouse help page: four reference topics, one shown at a time. */
(function () {
  'use strict';

  var panel = document.getElementById('guide-panel');

  var TOPICS = {
    clearance: {
      heading: 'Clearance levels',
      body:
        '<dl class="guide-list">' +
        '<dt>Cleared for broadcast</dt><dd>Any programme may play it, on air and in podcasts.</dd>' +
        '<dt>Station use only</dt><dd>On air at Kestrel FM only. Partner stations may not take it, whatever the partner setting says.</dd>' +
        '<dt>Restricted, ask the duty editor</dt><dd>Contributors or rights holders have set conditions. Check them before every use.</dd>' +
        '<dt>Pending clearance</dt><dd>Not yet checked. It must not go to air.</dd>' +
        '</dl>'
    },
    formats: {
      heading: 'Audio formats',
      body:
        '<p>Finished audio is kept as 48 kHz, 24-bit WAV with a broadcast wave header. The studio machines attach it; Reelhouse only holds the catalogue record.</p>' +
        '<p>Running times are read from the attached file. A record without audio shows "not timed yet".</p>' +
        '<p>Compressed copies for the website are made by the web desk from the WAV, never from a previous compressed copy.</p>'
    },
    contacts: {
      heading: 'Desk contacts',
      body:
        '<dl class="guide-list">' +
        '<dt>Duty editor</dt><dd>Clearance questions, restricted recordings, anything legal. Studio line 201.</dd>' +
        '<dt>Archive desk</dt><dd>Collections, shelf numbers and removals. Studio line 214.</dd>' +
        '<dt>Studio B</dt><dd>Live sessions and their paperwork. Studio line 230.</dd>' +
        '</dl>'
    },
    retention: {
      heading: 'Keeping and removing',
      body:
        '<p>Council meetings are kept in full for six years. Everything else is reviewed by the archive desk each winter.</p>' +
        '<p>Removing a recording in Reelhouse takes it off the shelf straight away. The audio itself is only deleted after the winter review.</p>' +
        '<p>Shelf numbers are not reused, so gaps in the numbering are normal.</p>'
    }
  };

  function showTopic(name) {
    var topic = TOPICS[name];
    panel.innerHTML = '<h2>' + topic.heading + '</h2>' + topic.body;
    Object.keys(TOPICS).forEach(function (other) {
      var button = document.getElementById('guide-' + other);
      if (other === name) {
        button.setAttribute('aria-pressed', 'true');
      } else {
        button.setAttribute('aria-pressed', 'false');
      }
    });
  }

  Object.keys(TOPICS).forEach(function (name) {
    document.getElementById('guide-' + name).addEventListener('click', function () { showTopic(name); });
  });

  showTopic('clearance');
}());
